import { FMP_RATE_LIMIT } from "./config";

type Task<T> = () => Promise<T>;

interface QueuedTask<T> {
  task: Task<T>;
  resolve: (value: T) => void;
  reject: (reason?: unknown) => void;
}

export class RateLimiter {
  private readonly queue: QueuedTask<unknown>[] = [];
  private readonly startedAt: number[] = [];
  private running = false;

  constructor(
    private readonly safePerSecond = FMP_RATE_LIMIT.safePerSecond,
    private readonly burstPerMinute = FMP_RATE_LIMIT.burstPerMinute,
  ) {}

  schedule<T>(task: Task<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      this.queue.push({ task, resolve: resolve as (value: unknown) => void, reject });
      void this.drain();
    });
  }

  status() {
    this.discardOld(Date.now());
    return {
      queued: this.queue.length,
      startedLastMinute: this.startedAt.length,
      safePerSecond: this.safePerSecond,
      burstPerMinute: this.burstPerMinute,
    };
  }

  private async drain(): Promise<void> {
    if (this.running) return;
    this.running = true;

    try {
      while (this.queue.length > 0) {
        await this.waitForSlot();
        const item = this.queue.shift();
        if (!item) continue;

        void item.task().then(item.resolve).catch(item.reject);
      }
    } finally {
      this.running = false;
      if (this.queue.length > 0) void this.drain();
    }
  }

  private async waitForSlot(): Promise<void> {
    while (true) {
      const now = Date.now();
      this.discardOld(now);

      const waitForMinute =
        this.startedAt.length >= this.burstPerMinute ? 60_000 - (now - this.startedAt[0]) : 0;
      const waitForSecond =
        this.startedAt.length > 0 ? 1000 / this.safePerSecond - (now - this.startedAt[this.startedAt.length - 1]) : 0;
      const waitMs = Math.max(waitForMinute, waitForSecond, 0);

      if (waitMs <= 0) {
        this.startedAt.push(now);
        return;
      }
      await sleep(Math.min(waitMs, 1000));
    }
  }

  private discardOld(now: number): void {
    while (this.startedAt.length > 0 && now - this.startedAt[0] >= 60_000) {
      this.startedAt.shift();
    }
  }
}

export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export const fmpRateLimiter = new RateLimiter();
