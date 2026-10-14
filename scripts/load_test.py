from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import statistics
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

import socketio


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Exercise room creation and concurrent Socket.IO updates."
    )
    parser.add_argument("--url", default="http://127.0.0.1:5000")
    parser.add_argument("--clients", type=int, default=20)
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument(
        "--workload",
        choices=("separate-rooms", "shared-room"),
        default="separate-rooms",
        help="Stress independent room writes or shared-room vote broadcasts.",
    )
    args = parser.parse_args()
    maximum_clients = 50 if args.workload == "shared-room" else 500
    if not 1 <= args.clients <= maximum_clients:
        parser.error(f"--clients must be between 1 and {maximum_clients} for this workload.")
    if args.iterations < 1:
        parser.error("--iterations must be at least 1.")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than zero.")
    args.url = args.url.rstrip("/")
    return args


def _create_room(base_url: str, worker_id: int, timeout: float) -> dict[str, str]:
    payload = json.dumps(
        {
            "name": f"Load Host {worker_id}",
            "participant_id": f"load-{uuid.uuid4().hex}",
            "issue": "Load test story",
        }
    ).encode("utf-8")
    request = Request(
        f"{base_url}/api/rooms",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 201:
                raise RuntimeError(f"Room creation returned HTTP {response.status}.")
            return json.loads(response.read())
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Room creation returned HTTP {error.code}: {detail}"
        ) from error


def _join_payload(
    room: dict[str, str],
    worker_id: int,
    workload: str,
) -> dict[str, str]:
    is_shared_host = workload == "shared-room" and worker_id == 0
    is_owner = workload == "separate-rooms" or is_shared_host
    payload = {
        "room_id": room["room_id"],
        "name": f"Load Client {worker_id}",
        "participant_id": (
            room["participant_id"]
            if is_owner
            else f"load-{uuid.uuid4().hex}"
        ),
    }
    if is_owner:
        payload["participant_token"] = room["participant_token"]
        payload["host_token"] = room["host_token"]
    return payload


def _delete_room(
    client: socketio.Client,
    room: dict[str, str],
    worker_id: int,
    workload: str,
    timeout: float,
) -> None:
    if not client.connected:
        client.connect(
            room["_base_url"],
            transports=["polling"],
            wait_timeout=timeout,
        )
    client.call(
        "join_room",
        _join_payload(room, worker_id, workload),
        timeout=timeout,
    )
    client.call(
        "delete_session",
        {"host_token": room["host_token"]},
        timeout=timeout,
    )


def _percentile(samples: list[float], percentile: float) -> float | None:
    if not samples:
        return None
    ordered = sorted(samples)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 2)


def main() -> int:
    args = _parse_args()
    shared_room = None
    metrics: dict[str, list[float]] = {
        "room_create": [],
        "socket_connect": [],
        "socket_join": [],
        "room_update": [],
        "room_vote": [],
    }
    failures: list[dict[str, str | int]] = []
    remaining_rooms: set[str] = set()
    result_lock = threading.Lock()
    run_started_at = time.perf_counter()
    phase_times: dict[str, float | None] = {
        "operations_started": None,
        "operations_finished": None,
        "cleanup_started": None,
        "cleanup_finished": None,
    }
    operation_clients_finished = 0
    clients_cleaned_up = 0

    def mark_operations_started() -> None:
        phase_times["operations_started"] = time.perf_counter()

    def mark_operations_finished() -> None:
        nonlocal operation_clients_finished
        with result_lock:
            operation_clients_finished += 1
            if operation_clients_finished == args.clients:
                phase_times["operations_finished"] = time.perf_counter()

    def mark_cleanup_finished() -> None:
        nonlocal clients_cleaned_up
        with result_lock:
            clients_cleaned_up += 1
            if clients_cleaned_up == args.clients:
                phase_times["cleanup_finished"] = time.perf_counter()

    def mark_cleanup_started() -> None:
        phase_times["cleanup_started"] = time.perf_counter()

    cleanup_barrier = threading.Barrier(
        args.clients,
        action=mark_cleanup_started,
        timeout=args.timeout,
    )
    start_barrier = threading.Barrier(
        args.clients,
        action=mark_operations_started,
        timeout=args.timeout,
    )

    def record_latency(name: str, started_at: float) -> None:
        with result_lock:
            metrics[name].append((time.perf_counter() - started_at) * 1000)

    def record_failure(worker_id: int, error: Exception) -> None:
        with result_lock:
            failures.append(
                {
                    "client": worker_id,
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    def run_client(worker_id: int) -> None:
        client = socketio.Client(reconnection=False)
        room = shared_room
        joined = False
        operations_started = False
        if room is not None:
            room["_base_url"] = args.url
        try:
            started_at = time.perf_counter()
            client.connect(
                args.url,
                transports=["polling"],
                wait_timeout=args.timeout,
            )
            record_latency("socket_connect", started_at)

            if room is None:
                started_at = time.perf_counter()
                room = _create_room(args.url, worker_id, args.timeout)
                room["_base_url"] = args.url
                record_latency("room_create", started_at)

            started_at = time.perf_counter()
            client.call(
                "join_room",
                _join_payload(room, worker_id, args.workload),
                timeout=args.timeout,
            )
            joined = True
            record_latency("socket_join", started_at)
            start_barrier.wait()
            operations_started = True

            try:
                for iteration in range(args.iterations):
                    started_at = time.perf_counter()
                    if args.workload == "shared-room":
                        client.call(
                            "vote",
                            {"value": "5" if iteration % 2 == 0 else "8"},
                            timeout=args.timeout,
                        )
                        metric_name = "room_vote"
                    else:
                        client.call(
                            "update_issue",
                            {
                                "issue": f"Load test client {worker_id}, update {iteration}",
                                "host_token": room["host_token"],
                            },
                            timeout=args.timeout,
                        )
                        metric_name = "room_update"
                    record_latency(metric_name, started_at)
            finally:
                mark_operations_finished()
        except Exception as error:
            start_barrier.abort()
            record_failure(worker_id, error)
        finally:
            try:
                cleanup_barrier.wait()
            except threading.BrokenBarrierError as error:
                record_failure(worker_id, error)
            if room is not None and args.workload == "separate-rooms":
                try:
                    _delete_room(
                        client,
                        room,
                        worker_id,
                        args.workload,
                        args.timeout,
                    )
                except Exception as error:
                    with result_lock:
                        remaining_rooms.add(room["room_id"])
                    record_failure(worker_id, error)
            elif room is not None and args.workload == "shared-room" and worker_id == 0:
                try:
                    if joined and client.connected:
                        client.call(
                            "delete_session",
                            {"host_token": room["host_token"]},
                            timeout=args.timeout,
                        )
                    else:
                        _delete_room(
                            client,
                            room,
                            worker_id,
                            args.workload,
                            args.timeout,
                        )
                except Exception as error:
                    with result_lock:
                        remaining_rooms.add(room["room_id"])
                    record_failure(worker_id, error)
            try:
                if client.connected:
                    client.disconnect()
            finally:
                mark_cleanup_finished()

    if args.workload == "shared-room":
        create_started_at = time.perf_counter()
        shared_room = _create_room(args.url, 0, args.timeout)
        record_latency("room_create", create_started_at)

    with ThreadPoolExecutor(max_workers=args.clients) as executor:
        futures = [
            executor.submit(run_client, worker_id)
            for worker_id in range(args.clients)
        ]
        for future in futures:
            future.result()
    whole_run_seconds = time.perf_counter() - run_started_at
    operations_started_at = phase_times["operations_started"]
    operations_finished_at = phase_times["operations_finished"]
    cleanup_started_at = phase_times["cleanup_started"]
    cleanup_finished_at = phase_times["cleanup_finished"]
    operation_elapsed_seconds = (
        operations_finished_at - operations_started_at
        if operations_started_at is not None and operations_finished_at is not None
        else None
    )
    setup_seconds = (
        operations_started_at - run_started_at
        if operations_started_at is not None
        else None
    )
    cleanup_seconds = (
        cleanup_finished_at - cleanup_started_at
        if cleanup_finished_at is not None and cleanup_started_at is not None
        else None
    )

    latency_report = {}
    for name, samples in metrics.items():
        latency_report[name] = {
            "count": len(samples),
            "mean_ms": round(statistics.fmean(samples), 2) if samples else None,
            "p50_ms": _percentile(samples, 0.50),
            "p95_ms": _percentile(samples, 0.95),
            "p99_ms": _percentile(samples, 0.99),
        }

    operation_count = len(metrics["room_vote"]) + len(metrics["room_update"])
    print(
        json.dumps(
            {
                "target": args.url,
                "workload": args.workload,
                "clients": args.clients,
                "iterations_per_client": args.iterations,
                "timing_seconds": {
                    "setup": round(setup_seconds, 2) if setup_seconds is not None else None,
                    "operations": (
                        round(operation_elapsed_seconds, 2)
                        if operation_elapsed_seconds is not None
                        else None
                    ),
                    "cleanup": (
                        round(cleanup_seconds, 2)
                        if cleanup_seconds is not None
                        else None
                    ),
                    "whole_run": round(whole_run_seconds, 2),
                },
                "operation_throughput_per_second": round(
                    operation_count / operation_elapsed_seconds
                    if operation_elapsed_seconds
                    else 0,
                    2,
                ),
                "latency": latency_report,
                "failures": failures,
                "rooms_requiring_manual_cleanup": sorted(remaining_rooms),
            },
            indent=2,
        )
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
