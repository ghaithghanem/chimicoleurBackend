import logging
import os
import signal
import threading

from .buffer import Buffer
from .config import Config
from .core import Collector, Flusher
from .readers import FakePLCReader, Snap7Reader
from .sink import ApiSink


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("collector")
    cfg = Config.from_env()

    reader = (Snap7Reader(cfg.plc_ip, cfg.plc_rack, cfg.plc_slot) if cfg.reader == "snap7"
              else FakePLCReader(cfg.fake_rate_per_min, cfg.fake_disconnect_prob))
    buffer = Buffer(cfg.buffer_path)
    sink = ApiSink(cfg.api_url, cfg.ingest_api_key, cfg.api_verify_tls)
    collector = Collector(reader, cfg.tags, buffer, cfg.poll_interval_s, cfg.heartbeat_interval_s, cfg.max_backoff_s)
    flusher = Flusher(buffer, sink, cfg.flush_batch, cfg.flush_interval_s, cfg.max_backoff_s)

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())

    log.info("starting: reader=%s tags=%s api=%s buffered=%d",
             cfg.reader, [t.source for t in cfg.tags], cfg.api_url, buffer.count())
    t = threading.Thread(target=flusher.run, args=(stop,), name="flusher", daemon=True)
    t.start()
    collector.run(stop)
    t.join(timeout=15)
    sink.close()
    buffer.close()
    log.info("stopped")


if __name__ == "__main__":
    main()
