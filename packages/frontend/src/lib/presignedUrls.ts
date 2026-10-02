/**
 * S3 transfers through presigned URLs issued by the backend.
 *
 * The browser has no S3 permissions of its own: the backend checks the request
 * (project, caller's prefix, file type and size) and returns a URL that is
 * valid for 5 minutes and for one object only. Downloads therefore ask for a
 * fresh URL every time instead of signing one locally.
 */

export type FetchApi = <T>(path: string, init?: RequestInit) => Promise<T>;

export interface PresignedUrlResponse {
  url: string;
  expires_in: number;
}

const EXT_MIME: Record<string, string> = {
  dxf: 'application/dxf',
  // Structured data: browsers often leave file.type empty for these, so map by
  // extension to the MIME types the backend uses to classify datasets.
  csv: 'text/csv',
  tsv: 'text/tab-separated-values',
  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
};

/** Content type for an upload by extension, when the browser reports none. */
export const getMimeTypeByExt = (name: string): string => {
  const ext = name.split('.').pop()?.toLowerCase() || '';
  return EXT_MIME[ext] || 'application/octet-stream';
};

/**
 * Content type declared when requesting the upload URL. The PUT must send the
 * same value: it is a signed header of the presigned URL.
 */
export const uploadContentType = (file: Pick<File, 'name' | 'type'>): string =>
  file.type || getMimeTypeByExt(file.name);

/** Key of an `s3://bucket/key` URI; null for anything else or an empty key. */
export function s3KeyFromUri(uri: string | null | undefined): string | null {
  if (!uri?.startsWith('s3://')) return null;
  const slash = uri.indexOf('/', 5);
  if (slash < 0) return null;
  const key = uri.slice(slash + 1);
  return key || null;
}

/** API path of the document download URL (the backend picks the bucket). */
export const documentDownloadUrlPath = (projectId: string, key: string) =>
  `projects/${encodeURIComponent(projectId)}/documents/download-url?key=${encodeURIComponent(key)}`;

/** API path of the artifact download URL (key under the caller's prefix). */
export const artifactDownloadUrlPath = (key: string) =>
  `artifacts/download-url?key=${encodeURIComponent(key)}`;

/** Presigned GET for an object of the project in the document bucket. */
export async function requestDocumentDownloadUrl(
  fetchApi: FetchApi,
  projectId: string,
  key: string,
): Promise<string> {
  const { url } = await fetchApi<PresignedUrlResponse>(
    documentDownloadUrlPath(projectId, key),
  );
  return url;
}

/** Presigned GET for one of the caller's artifacts in the agent bucket. */
export async function requestArtifactDownloadUrl(
  fetchApi: FetchApi,
  key: string,
): Promise<string> {
  const { url } = await fetchApi<PresignedUrlResponse>(
    artifactDownloadUrlPath(key),
  );
  return url;
}

/**
 * PUT a file to the presigned upload URL the backend returned for it. The
 * browser sets Content-Length from the file; S3 checks it and Content-Type
 * against the signed values.
 */
export async function putToPresignedUrl(
  uploadUrl: string,
  file: File,
  fetchImpl: typeof fetch = fetch,
): Promise<void> {
  const response = await fetchImpl(uploadUrl, {
    method: 'PUT',
    body: file,
    headers: { 'Content-Type': uploadContentType(file) },
  });
  if (!response.ok) {
    throw new Error(`Failed to upload ${file.name} to S3`);
  }
}
