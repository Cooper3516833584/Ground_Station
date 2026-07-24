"""Deterministic FleetBus half-duplex timing simulator; no serial hardware."""

from dataclasses import dataclass
from typing import Dict, List, Optional


class SimulatedCollision(RuntimeError):
    pass


@dataclass(frozen=True)
class Transmission:
    endpoint: str
    started_at: float
    ended_at: float
    transaction: int


class VirtualHalfDuplexChannel:
    def __init__(self) -> None:
        self.transmissions = []  # type: List[Transmission]

    def transmit(
        self, endpoint: str, started_at: float, duration: float, transaction: int
    ) -> Transmission:
        if duration < 0:
            raise ValueError("duration must not be negative")
        ended_at = started_at + duration
        for active in self.transmissions[-3:]:
            if started_at < active.ended_at and active.started_at < ended_at:
                raise SimulatedCollision(
                    "{} overlaps {}".format(endpoint, active.endpoint)
                )
        value = Transmission(endpoint, started_at, ended_at, transaction)
        self.transmissions.append(value)
        return value


@dataclass
class SimulationStats:
    transactions: int = 0
    responses: int = 0
    timeouts: int = 0
    duplicates: int = 0
    collisions: int = 0


class SimulatedNode:
    def __init__(self, name: str, turnaround_s: float = 0.20) -> None:
        self.name = name
        self.turnaround_s = turnaround_s
        self.executions = {}  # type: Dict[int, int]

    def handle(self, seq: int, command: bool) -> None:
        if command:
            self.executions[seq] = self.executions.get(seq, 0) + 1


def run_simulation(
    transaction_count: int = 10000,
    *,
    turnaround_s: float = 0.20,
    response_timeout_s: float = 0.50,
    guard_s: float = 0.10,
    drop_every: int = 0,
) -> SimulationStats:
    channel = VirtualHalfDuplexChannel()
    nodes = (
        SimulatedNode("drone", turnaround_s),
        SimulatedNode("car", turnaround_s),
    )
    now = 0.0
    stats = SimulationStats()
    for index in range(transaction_count):
        node = nodes[index % 2]
        seq = index + 1
        command = index % 17 == 0
        stats.transactions += 1
        try:
            request = channel.transmit("ground", now, 0.002, index)
            node.handle(seq, command)
            reply_at = request.ended_at + node.turnaround_s
            dropped = bool(drop_every and (index + 1) % drop_every == 0)
            if dropped:
                stats.timeouts += 1
                now = request.ended_at + response_timeout_s + guard_s
                if command:
                    stats.duplicates += 1
                    node.handle(seq, False)
            else:
                reply = channel.transmit(node.name, reply_at, 0.002, index)
                stats.responses += 1
                now = reply.ended_at + guard_s
        except SimulatedCollision:
            stats.collisions += 1
            raise
    return stats


if __name__ == "__main__":
    result = run_simulation()
    print(result)
