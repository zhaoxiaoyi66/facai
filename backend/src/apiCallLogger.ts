import { mkdir, appendFile } from "node:fs/promises";
import { dirname, join } from "node:path";

export interface ApiCallLog {
  provider: "FMP";
  endpoint: string;
  symbol?: string;
  status: "success" | "failed" | "cache_hit";
  httpStatus?: number;
  durationMs: number;
  attempt: number;
  error?: string;
  createdAt: string;
}

export interface ApiCallLogSink {
  saveApiCallLog(entry: ApiCallLog): void | Promise<void>;
}

export class ApiCallLogger {
  constructor(
    private readonly logPath = join(process.cwd(), ".cache", "api_call_logs.jsonl"),
    private readonly sink?: ApiCallLogSink,
  ) {}

  async log(entry: ApiCallLog): Promise<void> {
    await mkdir(dirname(this.logPath), { recursive: true });
    await appendFile(this.logPath, JSON.stringify(entry) + "\n", "utf8");
    await this.sink?.saveApiCallLog(entry);
  }
}

export function createApiCallLoggerWithSink(sink: ApiCallLogSink, logPath?: string): ApiCallLogger {
  return new ApiCallLogger(logPath, sink);
}

export const apiCallLogger = new ApiCallLogger();
