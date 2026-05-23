import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { CACHE_TTL, CacheBucket } from "./config";

interface CacheRecord<T> {
  bucket: CacheBucket;
  payload: T;
  fetchedAt: string;
}

export class CacheService {
  constructor(private readonly cacheRoot = join(process.cwd(), ".cache", "fmp")) {}

  async get<T>(bucket: CacheBucket, keyParts: unknown[]): Promise<T | null> {
    const path = this.pathFor(bucket, keyParts);
    try {
      const raw = await readFile(path, "utf8");
      const record = JSON.parse(raw) as CacheRecord<T>;
      const fetchedAt = new Date(record.fetchedAt).getTime();
      if (Date.now() - fetchedAt > CACHE_TTL[bucket]) return null;
      return record.payload;
    } catch {
      return null;
    }
  }

  async set<T>(bucket: CacheBucket, keyParts: unknown[], payload: T): Promise<void> {
    const path = this.pathFor(bucket, keyParts);
    await mkdir(dirname(path), { recursive: true });
    const record: CacheRecord<T> = {
      bucket,
      payload,
      fetchedAt: new Date().toISOString(),
    };
    await writeFile(path, JSON.stringify(record), "utf8");
  }

  private pathFor(bucket: CacheBucket, keyParts: unknown[]): string {
    const digest = createHash("sha256").update(JSON.stringify(keyParts)).digest("hex");
    return join(this.cacheRoot, bucket, `${digest}.json`);
  }
}

export const cacheService = new CacheService();
