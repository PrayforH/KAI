import { useCallback, useRef, useState } from "react";

/**
 * One failed file in a batch. Kept per file so several failures stay visible
 * instead of the last one overwriting the rest.
 */
export type UploadFailure = { name: string; reason: string };

export type UploadQueueOptions<T> = {
  /** Upload a single file. Resolving `null` means the upload was rejected; any other value is success. */
  upload: (file: File) => Promise<T | null | void>;
  /** Called once per successful upload, in submission order. */
  onUploaded: (result: T, file: File) => void;
  /** Free attachment slots right now. Omit when the server owns the limit. */
  availableSlots?: () => number;
  /** Copy shown when files are dropped because the attachment limit is full. */
  limitNotice?: (ignored: number) => string;
  failureMessage?: (reason: unknown) => string;
  /** Copy used when `upload` resolves `null`. */
  rejectedMessage?: (file: File) => string;
};

export type UploadQueue = {
  /** Files uploading or waiting in the queue. */
  pending: number;
  /** Failures from the current batch, one line per file. */
  failures: UploadFailure[];
  /** Result of the last finished batch, e.g. 「已上传 3 个文件」. */
  summary: string;
  /** Files refused because the attachment limit was full. */
  notice: string;
  enqueue: (files: readonly File[]) => void;
  dismiss: () => void;
};

const DEFAULT_REJECTED = "上传失败，请重试";

function reasonText(reason: unknown): string {
  if (reason instanceof Error && reason.message) return reason.message;
  return typeof reason === "string" && reason ? reason : DEFAULT_REJECTED;
}

/**
 * Serial upload queue shared by every composer.
 *
 * Files run one at a time, so a second paste mid-upload joins the queue instead
 * of being silently dropped. The queue owns its attachment-slot reservations:
 * files that no longer fit are never uploaded, and each failure stays on its
 * own line until the next batch starts.
 */
export function useUploadQueue<T>(options: UploadQueueOptions<T>): UploadQueue {
  const optionsRef = useRef(options);
  optionsRef.current = options;

  /** Tail of the upload chain; each file waits for the previous one. */
  const chainRef = useRef<Promise<void>>(Promise.resolve());
  /** Slots held by queued or running uploads, on top of the caller's count. */
  const reservedRef = useRef(0);
  const pendingRef = useRef(0);
  const batchRef = useRef({ total: 0, done: 0, uploaded: 0, failed: 0 });

  const [pending, setPending] = useState(0);
  const [failures, setFailures] = useState<UploadFailure[]>([]);
  const [summary, setSummary] = useState("");
  const [notice, setNotice] = useState("");

  const enqueue = useCallback((input: readonly File[]) => {
    const files = Array.from(input);
    if (!files.length) return;

    const { availableSlots, limitNotice } = optionsRef.current;
    let accepted = files;
    if (availableSlots) {
      const slots = Math.max(0, availableSlots() - reservedRef.current);
      accepted = files.slice(0, slots);
      setNotice(accepted.length < files.length && limitNotice ? limitNotice(files.length - accepted.length) : "");
    } else {
      setNotice("");
    }
    if (!accepted.length) return;

    // A new batch starts only when nothing is in flight; a paste during an
    // upload appends to the running batch so earlier failures are not erased.
    if (pendingRef.current === 0) {
      batchRef.current = { total: 0, done: 0, uploaded: 0, failed: 0 };
      setFailures([]);
      setSummary("");
    }
    batchRef.current.total += accepted.length;
    reservedRef.current += accepted.length;
    pendingRef.current += accepted.length;
    setPending(pendingRef.current);

    for (const file of accepted) {
      chainRef.current = chainRef.current.then(() => uploadOne(file));
    }

    async function uploadOne(file: File): Promise<void> {
      const current = optionsRef.current;
      try {
        const result = await current.upload(file);
        if (result === null) {
          batchRef.current.failed += 1;
          appendFailure({
            name: file.name,
            reason: current.rejectedMessage?.(file) ?? DEFAULT_REJECTED,
          });
        } else {
          current.onUploaded(result as T, file);
          batchRef.current.uploaded += 1;
        }
      } catch (reason) {
        batchRef.current.failed += 1;
        appendFailure({
          name: file.name,
          reason: current.failureMessage ? current.failureMessage(reason) : reasonText(reason),
        });
      } finally {
        reservedRef.current = Math.max(0, reservedRef.current - 1);
        pendingRef.current = Math.max(0, pendingRef.current - 1);
        batchRef.current.done += 1;
        setPending(pendingRef.current);
        if (batchRef.current.done >= batchRef.current.total) {
          const { uploaded, failed } = batchRef.current;
          const parts = [
            uploaded > 0 ? `已上传 ${uploaded} 个文件` : "",
            failed > 0 ? `${failed} 个失败` : "",
          ].filter(Boolean);
          setSummary(parts.join("，"));
        }
      }
    }

    function appendFailure(failure: UploadFailure) {
      setFailures((current) => [...current, failure]);
    }
  }, []);

  const dismiss = useCallback(() => {
    setFailures([]);
    setSummary("");
    setNotice("");
  }, []);

  return { pending, failures, summary, notice, enqueue, dismiss };
}
