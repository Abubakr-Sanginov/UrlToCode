import type { StateStorage } from "zustand/middleware";

/**
 * IndexedDB-backed storage for zustand's `persist` middleware.
 *
 * A cloned site is megabytes of generated code across many commits, which does
 * not fit the ~5 MB localStorage budget, so persistence needs a real database.
 *
 * Writes are debounced: `appendCommitCode` fires on every streamed token, and
 * serialising the whole project per token would make generation crawl. The last
 * write within a window wins, so a reload during a stream still lands the most
 * recent snapshot.
 */

const DB_NAME = "urltocode";
const DB_VERSION = 1;
const STORE_NAME = "kv";

// Long enough to coalesce a token stream into a few writes, short enough that
// closing the tab right after an edit still keeps it.
const WRITE_DEBOUNCE_MS = 1000;

let dbPromise: Promise<IDBDatabase | null> | null = null;

function openDb(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise;

  dbPromise = new Promise((resolve) => {
    if (typeof indexedDB === "undefined") {
      resolve(null);
      return;
    }
    let request: IDBOpenDBRequest;
    try {
      request = indexedDB.open(DB_NAME, DB_VERSION);
    } catch {
      resolve(null);
      return;
    }
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME);
      }
    };
    request.onsuccess = () => resolve(request.result);
    // A blocked or failed open degrades to "no persistence" rather than
    // breaking the app; in-memory state keeps working either way.
    request.onerror = () => resolve(null);
    request.onblocked = () => resolve(null);
  });

  return dbPromise;
}

async function readKey(key: string): Promise<string | null> {
  const db = await openDb();
  if (!db) return null;
  return new Promise((resolve) => {
    try {
      const request = db
        .transaction(STORE_NAME, "readonly")
        .objectStore(STORE_NAME)
        .get(key);
      request.onsuccess = () => {
        const value: unknown = request.result;
        resolve(typeof value === "string" ? value : null);
      };
      request.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}

async function writeKey(key: string, value: string): Promise<void> {
  const db = await openDb();
  if (!db) return;
  await new Promise<void>((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readwrite");
      tx.objectStore(STORE_NAME).put(value, key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
      tx.onabort = () => resolve();
    } catch {
      resolve();
    }
  });
}

async function deleteKey(key: string): Promise<void> {
  const db = await openDb();
  if (!db) return;
  await new Promise<void>((resolve) => {
    try {
      const tx = db.transaction(STORE_NAME, "readwrite");
      tx.objectStore(STORE_NAME).delete(key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => resolve();
      tx.onabort = () => resolve();
    } catch {
      resolve();
    }
  });
}

/** Zustand `StateStorage` backed by IndexedDB, with debounced writes. */
export function createIdbStorage(): StateStorage {
  const pending = new Map<string, string>();
  const timers = new Map<string, ReturnType<typeof setTimeout>>();

  function flush(key: string): void {
    const timer = timers.get(key);
    if (timer) {
      clearTimeout(timer);
      timers.delete(key);
    }
    const value = pending.get(key);
    if (value === undefined) return;
    pending.delete(key);
    void writeKey(key, value);
  }

  function schedule(key: string): void {
    const existing = timers.get(key);
    if (existing) clearTimeout(existing);
    const timer = setTimeout(() => flush(key), WRITE_DEBOUNCE_MS);
    // Browsers return a number here, but under Node (tests, SSR) the handle
    // would keep the process alive past the last write.
    (timer as { unref?: () => void }).unref?.();
    timers.set(key, timer);
  }

  return {
    getItem: (name) => readKey(name),

    setItem: (name, value) => {
      pending.set(name, value);
      schedule(name);
    },

    // A removed key must not be resurrected by an already-scheduled write.
    removeItem: (name) => {
      flush(name);
      pending.delete(name);
      return deleteKey(name);
    },
  };
}
