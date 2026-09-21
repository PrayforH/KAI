/**
 * Live check of the knowledge document ownership rule on 174.
 *
 * Reads two accessible WeKnora sources, then asks each one for the other's document.
 * The engine addresses documents globally, so before the ownership check the foreign
 * id resolved and returned 200; it must now be a 404 while the source's own document
 * still reads 200. Read-only: no document is modified or deleted.
 *
 * Runs inside the console container, which already holds the API URL and its token.
 */

const base = process.env.HARNESS_API_URL || "http://api:8000";
const token = process.env.HARNESS_API_BEARER_TOKEN;

async function call(path, init = {}) {
  const response = await fetch(base + path, {
    ...init,
    headers: { Authorization: `Bearer ${token}`, ...(init.headers ?? {}) },
  });
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  return { status: response.status, body };
}

if (!token) {
  console.error("no API token in the container environment");
  process.exit(2);
}

const sources = await call("/v1/studio/knowledge/sources");
if (sources.status !== 200 || !Array.isArray(sources.body)) {
  console.error(`source list failed: HTTP ${sources.status}`);
  process.exit(1);
}
const weknora = sources.body.filter((source) => source.kind === "weknora").slice(0, 3);
if (weknora.length < 2) {
  console.error(`need two weknora sources, found ${weknora.length}`);
  process.exit(3);
}
const [first, second] = weknora;

const firstDocuments = await call(`/v1/studio/knowledge/sources/${first.reference}/documents`);
const secondDocuments = await call(`/v1/studio/knowledge/sources/${second.reference}/documents`);
const own = firstDocuments.body?.[0];
const foreign = secondDocuments.body?.[0];
if (!own || !foreign) {
  console.error("one of the sources has no documents to compare");
  process.exit(4);
}

const ownRead = await call(
  `/v1/studio/knowledge/sources/${first.reference}/documents/${own.documentId}`,
);
const foreignRead = await call(
  `/v1/studio/knowledge/sources/${first.reference}/documents/${foreign.documentId}`,
);
const sameBaseRead = await call(
  `/v1/studio/knowledge/sources/${second.reference}/documents/${foreign.documentId}`,
);
const foreignChunks = await call(
  `/v1/studio/knowledge/sources/${first.reference}/documents/${foreign.documentId}/chunks`,
);

const report = {
  sourceA: first.reference,
  sourceB: second.reference,
  ownDocument: { id: own.documentId, status: ownRead.status },
  foreignDocumentViaA: { id: foreign.documentId, status: foreignRead.status },
  foreignDocumentViaItsOwnSource: { id: foreign.documentId, status: sameBaseRead.status },
  foreignDocumentChunksViaA: { id: foreign.documentId, status: foreignChunks.status },
  verdict:
    ownRead.status === 200 &&
    foreignRead.status === 404 &&
    sameBaseRead.status === 200 &&
    foreignChunks.status === 404
      ? "PASS"
      : "FAIL",
};
console.log(JSON.stringify(report, null, 2));
process.exit(report.verdict === "PASS" ? 0 : 1);
