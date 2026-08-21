import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
RADIO_SOURCE = ROOT / "com.enigmeta.foxtrot" / "assets" / "trot_radio.py"


class FakeTime(types.ModuleType):
    def __init__(self):
        super().__init__("time")
        self.now = 1000

    def ticks_ms(self):
        return self.now

    @staticmethod
    def ticks_diff(a, b):
        return a - b

    @staticmethod
    def ticks_add(a, b):
        return a + b

    def sleep_ms(self, delay):
        self.now += delay


def load_radio_module(fake_time):
    name = "trot_radio_rx_tx_test"
    spec = importlib.util.spec_from_file_location(name, RADIO_SOURCE)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"time": fake_time}):
        spec.loader.exec_module(module)
    return module


class TransitionChip:
    """A strict SX1262 transition fake.

    It accepts a TX only after RX was explicitly left through STDBY_RC and
    DIO2 automatic switch control was reaffirmed. That is the conservative
    sequence used by current RadioLib and avoids depending on an implicit
    RX->TX transition in the badge's older Python port.
    """

    def __init__(self, module, irq=0):
        self.module = module
        self.irq = irq
        self.events = []
        self.mode = "rx"
        self.dio2_auto = False
        self.packet = None
        self.irq_reads = []

    def getIrqStatus(self):
        self.events.append("irq")
        if self.irq_reads:
            return self.irq_reads.pop(0)
        return self.irq

    def getPacketType(self):
        self.events.append("packet-type")
        return self.module.PACKET_TYPE_LORA

    def standby(self):
        self.events.append("standby")
        self.mode = "standby"
        return 0

    def setDio2AsRfSwitch(self, enabled):
        self.events.append("dio2-auto:%s" % enabled)
        self.dio2_auto = enabled
        return 0

    def send(self, data):
        self.events.append("send")
        if self.mode == "standby" and self.dio2_auto:
            self.mode = "tx"
            self.irq = self.module.IRQ_TX_DONE
        else:
            self.irq = 0b1000000000  # SX1262 TIMEOUT
        return len(data), 0

    def clearIrqStatus(self):
        self.events.append("clear")
        self.irq = 0
        return 0

    def startReceive(self):
        self.events.append("start-rx")
        self.mode = "rx"
        return 0

    def recv(self):
        self.events.append("recv")
        packet = self.packet
        self.packet = None
        self.irq = 0
        self.mode = "rx"
        return packet, 0

    def getPacketStatus(self):
        return 80 << 16


class RxToTxTests(unittest.TestCase):
    def setUp(self):
        self.time = FakeTime()
        self.module = load_radio_module(self.time)
        self.link = self.module.TrotRadio()
        self.chip = TransitionChip(self.module)
        self.link.radio = self.chip
        self.link.ready = True

    def test_explicitly_leaves_rx_and_reasserts_automatic_dio2_before_tx(self):
        sent = self.link._transmit(self.module.build_beacon(self.link.fid()))

        self.assertTrue(sent)
        self.assertEqual(self.link.tx_fails, 0)
        self.assertLess(
            self.chip.events.index("standby"), self.chip.events.index("send")
        )
        self.assertLess(
            self.chip.events.index("dio2-auto:True"), self.chip.events.index("send")
        )
        self.assertEqual(self.chip.mode, "rx")

    def test_suspect_rx_latch_defers_tx_instead_of_overwriting_it(self):
        self.chip.packet = b"waiting"
        self.chip.irq = self.module.IRQ_RX_ANY
        self.chip.irq_reads = [self.module.IRQ_RX_ANY, 0]

        sent = self.link._transmit(self.module.build_beacon(self.link.fid()))

        self.assertFalse(sent)
        self.assertNotIn("send", self.chip.events)
        self.assertEqual(self.chip.packet, b"waiting")

    def test_valid_pending_rx_is_drained_before_tx(self):
        self.chip.packet = bytes([self.module.TYPE_BEACON << 4, 0x09])
        self.chip.irq = self.module.IRQ_RX_ANY

        sent = self.link._transmit(self.module.build_beacon(self.link.fid()))

        self.assertTrue(sent)
        self.assertLess(self.chip.events.index("recv"), self.chip.events.index("send"))
        self.assertIsNone(self.chip.packet)

    def test_enabling_tx_starts_a_fresh_burst_instead_of_reusing_silent_time(self):
        self.link.beaconing = False
        self.link._burst_at = 0
        self.time.now = self.module.T_BURST + 100
        self.link._last_health_check = self.time.now

        self.link.set_beaconing(True)
        self.link.poll()

        self.assertIn("send", self.chip.events)
        self.assertEqual(self.link.beacons_sent, 1)


if __name__ == "__main__":
    unittest.main()
