const DEFAULT_RETRY_LIMIT = 3;
const CANCELLED_MESSAGE = "Upload cancelled.";
const CANONICAL_ZIP_TYPE = "application/zip";
const ZIP_MIME_TYPES = new Set([
  CANONICAL_ZIP_TYPE,
  "application/x-zip-compressed",
  "application/octet-stream",
  "",
]);
const ZIP_CATEGORIES = new Set(["photographs", "archive"]);

export const getDeliveryUploadContentType = (file, category) => {
  const filename = String(file?.name || "");
  const browserType = String(file?.type || "").trim().toLowerCase();
  if (
    /\.zip$/i.test(filename) &&
    ZIP_CATEGORIES.has(String(category || "")) &&
    ZIP_MIME_TYPES.has(browserType)
  ) {
    return CANONICAL_ZIP_TYPE;
  }
  return browserType || "application/octet-stream";
};

export class DeliveryUploadCancelledError extends Error {
  constructor(message = CANCELLED_MESSAGE) {
    super(message);
    this.name = "DeliveryUploadCancelledError";
  }
}

export class DeliveryUploadError extends Error {
  constructor(code, message, { status } = {}) {
    super(message);
    this.name = "DeliveryUploadError";
    this.code = code;
    this.status = status;
  }
}

const isCancellation = (error) =>
  error instanceof DeliveryUploadCancelledError ||
  error?.name === "AbortError";

const safeBackendDetail = (detail, fallback) => {
  if (typeof detail !== "string") return fallback;
  const normalized = detail.replace(/\s+/g, " ").trim();
  if (
    !normalized ||
    normalized.length > 240 ||
    /https?:\/\/|x-amz-|credential|signature|secret|token/i.test(normalized) ||
    /(?:[A-Za-z0-9_-]+\/){2,}/.test(normalized)
  ) {
    return fallback;
  }
  return normalized;
};

const phaseFallback = {
  start:
    "Could not start the upload. Accepted delivery formats are JPG, WebP, MP4, PDF and ZIP.",
  "part-url":
    "Could not authorise an upload part. The upload session may have expired.",
  complete:
    "The transfer finished, but completion or object verification failed.",
  abort: "The upload could not be cancelled cleanly.",
};

export const createDeliveryUploadApi = ({
  csrfToken,
  basePath = "/api/real-estate/delivery/uploads",
  fetchImpl = globalThis.fetch,
}) => {
  if (typeof fetchImpl !== "function") {
    throw new Error("A fetch implementation is required.");
  }

  return async (phase, payload) => {
    const fallback = phaseFallback[phase] ?? "The upload request failed.";
    let response;
    try {
      response = await fetchImpl(`${basePath}/${phase}/`, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        body: JSON.stringify(payload),
      });
    } catch (error) {
      if (isCancellation(error)) throw new DeliveryUploadCancelledError();
      throw new DeliveryUploadError(`backend_${phase}`, fallback);
    }

    let result = {};
    try {
      const parsed = await response.json();
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        result = parsed;
      }
    } catch {
      // Never surface raw backend response text in the staff UI.
    }

    if (!response.ok) {
      throw new DeliveryUploadError(
        `backend_${phase}`,
        safeBackendDetail(result.detail, fallback),
        { status: response.status },
      );
    }
    return result;
  };
};

export const boundWorkerCount = (serverLimit, totalParts) => {
  const normalizedParts = Math.max(1, Math.floor(Number(totalParts) || 1));
  const normalizedLimit = Math.max(1, Math.floor(Number(serverLimit) || 1));
  return Math.min(normalizedLimit, normalizedParts);
};

export const sortCompletedParts = (completedParts) =>
  Array.from(completedParts.entries())
    .sort(([partA], [partB]) => partA - partB)
    .map(([part_number, etag]) => ({ part_number, etag }));

export const buildProgressSnapshot = ({
  partProgress,
  totalBytes,
  completedParts,
  totalParts,
}) => {
  const summedBytes = Array.from(partProgress.values()).reduce(
    (sum, current) => sum + Math.max(0, Number(current) || 0),
    0,
  );
  const bytesUploaded = Math.min(Math.max(0, totalBytes), summedBytes);
  return {
    bytesUploaded,
    totalBytes,
    percentage:
      totalBytes > 0
        ? Math.min(100, Math.max(0, (bytesUploaded / totalBytes) * 100))
        : 0,
    completedParts,
    totalParts,
  };
};

export const formatUploadBytes = (bytes) => {
  const safeBytes = Math.max(0, Number(bytes) || 0);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = safeBytes;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const fractionDigits = value >= 10 || unitIndex === 0 ? 0 : 1;
  return `${value.toFixed(fractionDigits)} ${units[unitIndex]}`;
};

export const uploadPartWithXhr = ({
  blob,
  contentType,
  onProgress,
  signal,
  url,
  xhrFactory = () => new XMLHttpRequest(),
}) =>
  new Promise((resolve, reject) => {
    const xhr = xhrFactory();
    let settled = false;

    const cleanup = () => {
      signal.removeEventListener("abort", handleSignalAbort);
    };
    const rejectOnce = (error) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(error);
    };
    const resolveOnce = (value) => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(value);
    };
    const handleSignalAbort = () => {
      xhr.abort();
      rejectOnce(new DeliveryUploadCancelledError());
    };

    xhr.upload.onprogress = (event) => {
      onProgress(Math.min(blob.size, Math.max(0, event.loaded)));
    };
    xhr.onerror = () => {
      rejectOnce(
        new DeliveryUploadError(
          "storage_network",
          "A storage request was blocked or interrupted. Check the network and private R2 CORS/preflight policy.",
        ),
      );
    };
    xhr.onabort = () => rejectOnce(new DeliveryUploadCancelledError());
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const etag = String(xhr.getResponseHeader("ETag") || "").trim();
        if (!etag) {
          rejectOnce(
            new DeliveryUploadError(
              "missing_etag",
              "R2 completed a part but did not expose its ETag. Check the private bucket CORS policy.",
            ),
          );
          return;
        }
        resolveOnce({ etag });
        return;
      }
      if (xhr.status === 401 || xhr.status === 403) {
        rejectOnce(
          new DeliveryUploadError(
            "presigned_rejected",
            "R2 rejected the presigned part URL. It may have expired; retry the upload.",
            { status: xhr.status },
          ),
        );
        return;
      }
      rejectOnce(
        new DeliveryUploadError(
          "storage_rejected",
          `R2 rejected an upload part with HTTP ${xhr.status || "error"}.`,
          { status: xhr.status },
        ),
      );
    };

    signal.addEventListener("abort", handleSignalAbort, { once: true });
    if (signal.aborted) {
      handleSignalAbort();
      return;
    }
    xhr.open("PUT", url);
    xhr.setRequestHeader("Content-Type", contentType);
    xhr.send(blob);
  });

export const getDeliveryUploadErrorMessage = (error) => {
  if (isCancellation(error)) return CANCELLED_MESSAGE;
  if (error instanceof DeliveryUploadError && error.message) {
    return error.message;
  }
  return "The upload failed. No deliverable was activated.";
};

export const createDeliveryMultipartUpload = ({
  api,
  file,
  startPayload,
  onProgress,
  onStatusChange,
  retryLimit = DEFAULT_RETRY_LIMIT,
  uploadPart = uploadPartWithXhr,
}) => {
  if (typeof api !== "function") throw new Error("An upload API is required.");
  if (!file || !Number.isFinite(file.size) || file.size <= 0) {
    throw new Error("A non-empty file is required.");
  }
  const uploadContentType =
    String(startPayload?.content_type || "").trim().toLowerCase() ||
    String(file.type || "").trim().toLowerCase() ||
    "application/octet-stream";

  const abortController = new AbortController();
  let startedUpload = null;
  let abortPromise = null;
  let completed = false;
  let completionStarted = false;
  let cancelled = false;
  let fatalError = null;

  const emitStatus = (phase, message) => {
    onStatusChange?.({ phase, message });
  };

  const abortServerOnce = async () => {
    if (!startedUpload || completed) return;
    if (!abortPromise) {
      abortPromise = Promise.resolve(
        api("abort", { upload_id: startedUpload.upload_id }),
      )
        .then(() => undefined)
        .catch(() => undefined);
    }
    await abortPromise;
  };

  const promise = (async () => {
    try {
      emitStatus("starting", "Preparing secure multipart upload…");
      try {
        startedUpload = await api("start", startPayload);
      } catch (error) {
        throw error instanceof DeliveryUploadError
          ? error
          : new DeliveryUploadError("backend_start", phaseFallback.start);
      }

      if (cancelled || abortController.signal.aborted) {
        await abortServerOnce();
        throw new DeliveryUploadCancelledError();
      }

      const partSize = Number(startedUpload.part_size);
      if (!Number.isFinite(partSize) || partSize <= 0) {
        throw new DeliveryUploadError(
          "invalid_start",
          "The server returned an invalid multipart upload configuration.",
        );
      }

      const totalParts = Math.ceil(file.size / partSize);
      const workerCount = boundWorkerCount(
        startedUpload.max_concurrency,
        totalParts,
      );
      const completedParts = new Map();
      const partProgress = new Map();
      let nextPartNumber = 1;

      const emitProgress = () => {
        onProgress?.(
          buildProgressSnapshot({
            partProgress,
            totalBytes: file.size,
            completedParts: completedParts.size,
            totalParts,
          }),
        );
      };

      const uploadSinglePart = async (partNumber) => {
        const start = (partNumber - 1) * partSize;
        const end = Math.min(file.size, start + partSize);
        const blob = file.slice(
          start,
          end,
          uploadContentType,
        );
        let lastError = null;

        for (let attempt = 1; attempt <= retryLimit; attempt += 1) {
          if (cancelled || abortController.signal.aborted) {
            throw new DeliveryUploadCancelledError();
          }
          partProgress.set(partNumber, 0);
          emitProgress();
          emitStatus(
            "uploading",
            `Uploading ${completedParts.size} of ${totalParts} parts${
              attempt > 1
                ? `; retrying part ${partNumber} (${attempt}/${retryLimit})`
                : ""
            }…`,
          );

          try {
            const signed = await api("part-url", {
              upload_id: startedUpload.upload_id,
              part_number: partNumber,
            });
            if (typeof signed.url !== "string" || !signed.url) {
              throw new DeliveryUploadError(
                "backend_part-url",
                phaseFallback["part-url"],
              );
            }
            const result = await uploadPart({
              blob,
              contentType: uploadContentType,
              onProgress: (loadedBytes) => {
                partProgress.set(
                  partNumber,
                  Math.min(blob.size, Math.max(0, Number(loadedBytes) || 0)),
                );
                emitProgress();
              },
              signal: abortController.signal,
              url: signed.url,
            });
            const etag = String(result?.etag || "").trim();
            if (!etag) {
              throw new DeliveryUploadError(
                "missing_etag",
                "R2 completed a part but did not expose its ETag. Check the private bucket CORS policy.",
              );
            }
            partProgress.set(partNumber, blob.size);
            completedParts.set(partNumber, etag);
            emitProgress();
            return;
          } catch (error) {
            if (cancelled || abortController.signal.aborted) {
              throw new DeliveryUploadCancelledError();
            }
            lastError = error;
            if (attempt === retryLimit) break;
          }
        }

        fatalError =
          lastError instanceof Error
            ? lastError
            : new DeliveryUploadError(
                "part_failed",
                `Upload part ${partNumber} failed after ${retryLimit} attempts.`,
              );
        abortController.abort();
        throw fatalError;
      };

      const workers = Array.from({ length: workerCount }, async () => {
        while (!fatalError && !cancelled && !abortController.signal.aborted) {
          const partNumber = nextPartNumber;
          nextPartNumber += 1;
          if (partNumber > totalParts) return;
          await uploadSinglePart(partNumber);
        }
      });

      await Promise.all(workers);
      if (
        cancelled ||
        abortController.signal.aborted ||
        completedParts.size !== totalParts
      ) {
        throw cancelled
          ? new DeliveryUploadCancelledError()
          : fatalError ??
              new DeliveryUploadError(
                "incomplete",
                "Not every upload part completed successfully.",
              );
      }

      emitProgress();
      emitStatus(
        "finalising",
        "Transfer complete. Finalising and verifying the uploaded object…",
      );
      if (cancelled || abortController.signal.aborted) {
        throw new DeliveryUploadCancelledError();
      }

      completionStarted = true;
      let completion;
      try {
        completion = await api("complete", {
          upload_id: startedUpload.upload_id,
          parts: sortCompletedParts(completedParts),
        });
      } catch (error) {
        throw error instanceof DeliveryUploadError
          ? error
          : new DeliveryUploadError("backend_complete", phaseFallback.complete);
      }

      completed = true;
      emitStatus("complete", "Upload verified and added to this delivery.");
      return { startedUpload, completion };
    } catch (error) {
      if (cancelled || isCancellation(error)) {
        await abortServerOnce();
        throw new DeliveryUploadCancelledError();
      }
      abortController.abort();
      await abortServerOnce();
      throw fatalError ?? error;
    }
  })();

  return {
    promise,
    cancel: async () => {
      if (completed || completionStarted || cancelled) return;
      cancelled = true;
      abortController.abort();
      await abortServerOnce();
    },
  };
};
