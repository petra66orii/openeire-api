import assert from "node:assert/strict";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";

import {
  buildProgressSnapshot,
  createDeliveryMultipartUpload,
  createDeliveryUploadApi,
  DeliveryUploadCancelledError,
  DeliveryUploadError,
  uploadPartWithXhr,
} from "../../static/realestate/js/delivery_multipart_uploader.mjs";

const makeFile = (size, type = "video/mp4") => ({
  name: "property-video.mp4",
  size,
  type,
  slice(start, end, contentType) {
    return { size: end - start, type: contentType };
  },
});

const partNumberFromUrl = (url) =>
  Number(new URL(url).searchParams.get("part"));

const waitUntil = async (predicate) => {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (predicate()) return;
    await delay(1);
  }
  throw new Error("Timed out waiting for test condition.");
};

const deferred = () => {
  let resolve;
  const promise = new Promise((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
};

const makeApi = ({
  maxConcurrency = 4,
  partSize = 10,
  onAbort,
  onComplete,
  onPartUrl,
} = {}) => {
  const urlAttempts = new Map();
  const calls = [];
  const api = async (phase, payload) => {
    calls.push({ phase, payload });
    if (phase === "start") {
      return {
        upload_id: "test-upload-id",
        part_size: partSize,
        max_concurrency: maxConcurrency,
      };
    }
    if (phase === "part-url") {
      const attempts = (urlAttempts.get(payload.part_number) || 0) + 1;
      urlAttempts.set(payload.part_number, attempts);
      onPartUrl?.(payload.part_number, attempts);
      return {
        url:
          `https://storage.invalid/upload?part=${payload.part_number}` +
          `&attempt=${attempts}`,
      };
    }
    if (phase === "complete") {
      onComplete?.(payload);
      return { success: true };
    }
    if (phase === "abort") {
      onAbort?.(payload);
      return { success: true };
    }
    throw new Error(`Unexpected API phase: ${phase}`);
  };
  return { api, calls, urlAttempts };
};

const runUpload = ({
  api,
  file = makeFile(50),
  onProgress,
  onStatusChange,
  retryLimit,
  uploadPart,
}) =>
  createDeliveryMultipartUpload({
    api,
    file,
    startPayload: {
      delivery_id: 17,
      filename: file.name,
      display_name: "Property video",
      category: "video",
      content_type: file.type,
      file_size: file.size,
    },
    onProgress,
    onStatusChange,
    retryLimit,
    uploadPart,
  });

test("honours the server worker limit and uploads every part once", async () => {
  const { api, calls } = makeApi({ maxConcurrency: 3 });
  let active = 0;
  let maximumActive = 0;
  const uploadedParts = [];

  const task = runUpload({
    api,
    uploadPart: async ({ blob, onProgress, url }) => {
      const partNumber = partNumberFromUrl(url);
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      uploadedParts.push(partNumber);
      onProgress(blob.size);
      await delay(3);
      active -= 1;
      return { etag: `etag-${partNumber}` };
    },
  });

  await task.promise;
  assert.equal(maximumActive, 3);
  assert.deepEqual(uploadedParts.toSorted((a, b) => a - b), [1, 2, 3, 4, 5]);
  assert.equal(
    calls.filter(({ phase }) => phase === "part-url").length,
    5,
  );
});

test("bounds concurrency to the number of parts", async () => {
  const { api } = makeApi({ maxConcurrency: 20, partSize: 10 });
  let active = 0;
  let maximumActive = 0;

  const task = runUpload({
    api,
    file: makeFile(25),
    uploadPart: async ({ url }) => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      await delay(2);
      active -= 1;
      return { etag: `etag-${partNumberFromUrl(url)}` };
    },
  });

  await task.promise;
  assert.equal(maximumActive, 3);
});

test("sends completion parts in ascending order after out-of-order finishes", async () => {
  let completionPayload = null;
  const { api } = makeApi({
    maxConcurrency: 4,
    onComplete: (payload) => {
      completionPayload = payload;
    },
  });

  const task = runUpload({
    api,
    file: makeFile(40),
    uploadPart: async ({ url }) => {
      const partNumber = partNumberFromUrl(url);
      await delay((5 - partNumber) * 2);
      return { etag: `etag-${partNumber}` };
    },
  });
  await task.promise;

  assert.deepEqual(completionPayload.parts, [
    { part_number: 1, etag: "etag-1" },
    { part_number: 2, etag: "etag-2" },
    { part_number: 3, etag: "etag-3" },
    { part_number: 4, etag: "etag-4" },
  ]);
});

test("retries a failed part with a fresh presigned URL", async () => {
  const seenUrls = [];
  const { api, urlAttempts } = makeApi();

  const task = runUpload({
    api,
    file: makeFile(10),
    uploadPart: async ({ url }) => {
      seenUrls.push(url);
      if (seenUrls.length === 1) {
        throw new DeliveryUploadError("storage_network", "Network interrupted.");
      }
      return { etag: "etag-1" };
    },
  });
  await task.promise;

  assert.equal(urlAttempts.get(1), 2);
  assert.equal(seenUrls.length, 2);
  assert.notEqual(seenUrls[0], seenUrls[1]);
});

test("aggregates concurrent byte progress without exceeding the file size", () => {
  assert.deepEqual(
    buildProgressSnapshot({
      partProgress: new Map([
        [1, 7],
        [2, 8],
        [3, 100],
      ]),
      totalBytes: 25,
      completedParts: 1,
      totalParts: 3,
    }),
    {
      bytesUploaded: 25,
      totalBytes: 25,
      percentage: 100,
      completedParts: 1,
      totalParts: 3,
    },
  );
});

test("resets a part's progress before retrying", async () => {
  const snapshots = [];
  const { api } = makeApi();
  let attempt = 0;

  const task = runUpload({
    api,
    file: makeFile(10),
    onProgress: (snapshot) => snapshots.push(snapshot.bytesUploaded),
    uploadPart: async ({ blob, onProgress }) => {
      attempt += 1;
      if (attempt === 1) {
        onProgress(blob.size / 2);
        throw new DeliveryUploadError("storage_network", "Network interrupted.");
      }
      onProgress(blob.size);
      return { etag: "etag-1" };
    },
  });
  await task.promise;

  const firstPartial = snapshots.indexOf(5);
  assert.notEqual(firstPartial, -1);
  assert.equal(snapshots[firstPartial + 1], 0);
});

test("fatal failure stops scheduling, cancels active work and aborts once", async () => {
  let abortCalls = 0;
  let completionCalls = 0;
  const startedParts = [];
  const { api } = makeApi({
    maxConcurrency: 2,
    onAbort: () => {
      abortCalls += 1;
    },
    onComplete: () => {
      completionCalls += 1;
    },
  });

  const task = runUpload({
    api,
    file: makeFile(80),
    retryLimit: 3,
    uploadPart: ({ signal, url }) => {
      const partNumber = partNumberFromUrl(url);
      startedParts.push(partNumber);
      if (partNumber === 1) {
        return Promise.reject(
          new DeliveryUploadError("storage_rejected", "R2 rejected a part."),
        );
      }
      return new Promise((resolve, reject) => {
        signal.addEventListener(
          "abort",
          () => reject(new DeliveryUploadCancelledError()),
          { once: true },
        );
      });
    },
  });

  await assert.rejects(task.promise, /R2 rejected a part/);
  assert.deepEqual(startedParts, [1, 2, 1, 1]);
  assert.equal(abortCalls, 1);
  assert.equal(completionCalls, 0);
});

test("cancellation aborts active requests, aborts the backend once and never completes", async () => {
  let activeRequests = 0;
  let browserAborts = 0;
  let abortCalls = 0;
  let completionCalls = 0;
  const { api } = makeApi({
    maxConcurrency: 2,
    onAbort: () => {
      abortCalls += 1;
    },
    onComplete: () => {
      completionCalls += 1;
    },
  });

  const task = runUpload({
    api,
    uploadPart: ({ signal }) => {
      activeRequests += 1;
      return new Promise((resolve, reject) => {
        signal.addEventListener(
          "abort",
          () => {
            browserAborts += 1;
            reject(new DeliveryUploadCancelledError());
          },
          { once: true },
        );
      });
    },
  });

  await waitUntil(() => activeRequests === 2);
  await Promise.all([task.cancel(), task.cancel()]);
  await assert.rejects(task.promise, DeliveryUploadCancelledError);
  assert.equal(browserAborts, 2);
  assert.equal(abortCalls, 1);
  assert.equal(completionCalls, 0);
});

test("missing ETag fails safely and prevents completion", async () => {
  let completionCalls = 0;
  let abortCalls = 0;
  const { api } = makeApi({
    onAbort: () => {
      abortCalls += 1;
    },
    onComplete: () => {
      completionCalls += 1;
    },
  });

  const task = runUpload({
    api,
    file: makeFile(10),
    uploadPart: async () => ({ etag: "" }),
  });

  await assert.rejects(
    task.promise,
    /did not expose its ETag.*CORS policy/,
  );
  assert.equal(completionCalls, 0);
  assert.equal(abortCalls, 1);
});

test("the XHR transport reports a missing exposed ETag without leaking its URL", async () => {
  const signedUrl =
    "https://storage.invalid/private?X-Amz-Signature=not-a-real-signature";
  const xhr = {
    upload: {},
    status: 200,
    open() {},
    setRequestHeader() {},
    send() {
      this.onload();
    },
    abort() {
      this.onabort();
    },
    getResponseHeader() {
      return null;
    },
  };

  await assert.rejects(
    uploadPartWithXhr({
      blob: { size: 10 },
      contentType: "video/mp4",
      onProgress() {},
      signal: new AbortController().signal,
      url: signedUrl,
      xhrFactory: () => xhr,
    }),
    (error) => {
      assert.equal(error.code, "missing_etag");
      assert.equal(error.message.includes(signedUrl), false);
      return true;
    },
  );
});

test("the XHR transport aborts promptly and reports cancellation distinctly", async () => {
  const controller = new AbortController();
  let xhrAbortCalls = 0;
  const xhr = {
    upload: {},
    open() {},
    setRequestHeader() {},
    send() {},
    abort() {
      xhrAbortCalls += 1;
      this.onabort();
    },
    getResponseHeader() {
      return null;
    },
  };

  const promise = uploadPartWithXhr({
    blob: { size: 10 },
    contentType: "video/mp4",
    onProgress() {},
    signal: controller.signal,
    url: "https://storage.invalid/upload",
    xhrFactory: () => xhr,
  });
  controller.abort();

  await assert.rejects(promise, DeliveryUploadCancelledError);
  assert.equal(xhrAbortCalls, 1);
});

test("the XHR transport identifies network or CORS/preflight interruption", async () => {
  const xhr = {
    upload: {},
    open() {},
    setRequestHeader() {},
    send() {
      this.onerror();
    },
    abort() {
      this.onabort();
    },
    getResponseHeader() {
      return null;
    },
  };

  await assert.rejects(
    uploadPartWithXhr({
      blob: { size: 10 },
      contentType: "video/mp4",
      onProgress() {},
      signal: new AbortController().signal,
      url: "https://storage.invalid/upload",
      xhrFactory: () => xhr,
    }),
    (error) => {
      assert.equal(error.code, "storage_network");
      assert.match(error.message, /network.*CORS\/preflight/i);
      return true;
    },
  );
});

test("completion begins only after every part succeeds", async () => {
  const completedParts = new Set();
  let completionObserved = null;
  const { api } = makeApi({
    maxConcurrency: 3,
    onComplete: () => {
      completionObserved = new Set(completedParts);
    },
  });

  const task = runUpload({
    api,
    file: makeFile(30),
    uploadPart: async ({ url }) => {
      const partNumber = partNumberFromUrl(url);
      await delay(partNumber);
      completedParts.add(partNumber);
      return { etag: `etag-${partNumber}` };
    },
  });
  await task.promise;

  assert.deepEqual([...completionObserved].toSorted(), [1, 2, 3]);
});

test("cancel is disabled once server completion and verification has started", async () => {
  const completionGate = deferred();
  let abortCalls = 0;
  let completionCalls = 0;
  const { api: baseApi } = makeApi({
    onAbort: () => {
      abortCalls += 1;
    },
    onComplete: () => {
      completionCalls += 1;
    },
  });
  const api = async (phase, payload) => {
    const result = await baseApi(phase, payload);
    if (phase === "complete") await completionGate.promise;
    return result;
  };

  const task = runUpload({
    api,
    file: makeFile(10),
    uploadPart: async () => ({ etag: "etag-1" }),
  });

  await waitUntil(() => completionCalls === 1);
  await task.cancel();
  assert.equal(abortCalls, 0);
  completionGate.resolve();
  await task.promise;
});

test("backend errors do not expose signed URLs or private object keys", async () => {
  const unsafeDetails = [
    "https://storage.invalid/private?X-Amz-Signature=not-a-real-signature",
    "real-estate-deliveries/customer/private/video.mp4",
  ];

  for (const detail of unsafeDetails) {
    const api = createDeliveryUploadApi({
      csrfToken: "safe-test-value",
      fetchImpl: async () => ({
        ok: false,
        status: 500,
        json: async () => ({ detail }),
      }),
    });
    await assert.rejects(
      api("part-url", { upload_id: "test-upload-id", part_number: 1 }),
      (error) => {
        assert.equal(
          error.message,
          "Could not authorise an upload part. The upload session may have expired.",
        );
        assert.equal(error.message.includes(detail), false);
        return true;
      },
    );
  }
});

test("uses one worker when max_concurrency is one", async () => {
  let active = 0;
  let maximumActive = 0;
  const { api } = makeApi({ maxConcurrency: 1 });

  const task = runUpload({
    api,
    file: makeFile(30),
    uploadPart: async ({ url }) => {
      active += 1;
      maximumActive = Math.max(maximumActive, active);
      await delay(2);
      active -= 1;
      return { etag: `etag-${partNumberFromUrl(url)}` };
    },
  });
  await task.promise;

  assert.equal(maximumActive, 1);
});
