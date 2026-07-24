import unittest

from tools.fleet_bus_simulator import (
    SimulatedCollision,
    VirtualHalfDuplexChannel,
    run_simulation,
)


class FleetBusSimulatorTests(unittest.TestCase):
    def test_ten_thousand_transactions_have_no_collision(self):
        stats = run_simulation(10000)
        self.assertEqual(stats.transactions, 10000)
        self.assertEqual(stats.responses, 10000)
        self.assertEqual(stats.collisions, 0)

    def test_collision_detector_rejects_overlapping_transmitters(self):
        channel = VirtualHalfDuplexChannel()
        channel.transmit("ground", 0.0, 0.1, 1)
        with self.assertRaises(SimulatedCollision):
            channel.transmit("drone", 0.05, 0.1, 1)

    def test_dropped_command_ack_does_not_execute_command_twice(self):
        stats = run_simulation(200, drop_every=1)
        self.assertGreater(stats.timeouts, 0)
        self.assertGreater(stats.duplicates, 0)
        self.assertEqual(stats.collisions, 0)


if __name__ == "__main__":
    unittest.main()
