// @vitest-environment node
import {
  artifactDownloadUrlPath,
  documentDownloadUrlPath,
  getMimeTypeByExt,
  putToPresignedUrl,
  requestArtifactDownloadUrl,
  requestDocumentDownloadUrl,
  s3KeyFromUri,
  uploadContentType,
  type FetchApi,
} from './presignedUrls';

const PRESIGNED = {
  url: 'https://idp-v2-document-storage-test-ap-south-1.s3.ap-south-1.amazonaws.com/projects/proj_1/documents/d1/d1.pdf?X-Amz-Expires=300',
  expires_in: 300,
};

function fakeFetchApi(response: unknown = PRESIGNED) {
  const calls: { path: string; init?: RequestInit }[] = [];
  const fetchApi = (async (path: string, init?: RequestInit) => {
    calls.push({ path, init });
    return response;
  }) as FetchApi;
  return { fetchApi, calls };
}

describe('s3KeyFromUri', () => {
  it('returns the key of an s3:// URI (the bucket is dropped)', () => {
    expect(
      s3KeyFromUri('s3://doc-bucket/projects/proj_1/documents/d1/d1.pdf'),
    ).toBe('projects/proj_1/documents/d1/d1.pdf');
    expect(s3KeyFromUri('s3://b/a b/(1).pdf')).toBe('a b/(1).pdf');
  });

  it('returns null for anything that is not an s3:// URI with a key', () => {
    expect(s3KeyFromUri('https://example.com/x.pdf')).toBeNull();
    expect(s3KeyFromUri('s3://bucket-only')).toBeNull();
    expect(s3KeyFromUri('s3://bucket/')).toBeNull();
    expect(s3KeyFromUri('')).toBeNull();
    expect(s3KeyFromUri(undefined)).toBeNull();
    expect(s3KeyFromUri(null)).toBeNull();
  });
});

describe('download URL paths', () => {
  it('sends the key as one encoded query value to the project endpoint', () => {
    expect(
      documentDownloadUrlPath('proj_1', 'projects/proj_1/documents/d1/d1.pdf'),
    ).toBe(
      'projects/proj_1/documents/download-url?key=projects%2Fproj_1%2Fdocuments%2Fd1%2Fd1.pdf',
    );
  });

  it('encodes characters that would otherwise change the query', () => {
    const path = artifactDownloadUrlPath(
      'alice/proj_1/artifacts/art_1/a&b=c?#राहुल.docx',
    );
    expect(path).toBe(
      'artifacts/download-url?key=alice%2Fproj_1%2Fartifacts%2Fart_1%2Fa%26b%3Dc%3F%23%E0%A4%B0%E0%A4%BE%E0%A4%B9%E0%A5%81%E0%A4%B2.docx',
    );
    const key = new URL(path, 'https://api.invalid/').searchParams.get('key');
    expect(key).toBe('alice/proj_1/artifacts/art_1/a&b=c?#राहुल.docx');
  });

  it('encodes the project id as one path segment', () => {
    expect(documentDownloadUrlPath('a/b', 'k')).toBe(
      'projects/a%2Fb/documents/download-url?key=k',
    );
  });
});

describe('requesting download URLs from the backend', () => {
  it('returns the document URL the backend issued', async () => {
    const { fetchApi, calls } = fakeFetchApi();

    const url = await requestDocumentDownloadUrl(
      fetchApi,
      'proj_1',
      'projects/proj_1/documents/d1/d1.pdf',
    );

    expect(url).toBe(PRESIGNED.url);
    expect(calls).toEqual([
      {
        path: 'projects/proj_1/documents/download-url?key=projects%2Fproj_1%2Fdocuments%2Fd1%2Fd1.pdf',
        init: undefined,
      },
    ]);
  });

  it('returns the artifact URL the backend issued', async () => {
    const { fetchApi, calls } = fakeFetchApi({
      url: 'https://agent.example/alice/x.docx',
      expires_in: 300,
    });

    const url = await requestArtifactDownloadUrl(
      fetchApi,
      'alice/proj_1/artifacts/art_1/x.docx',
    );

    expect(url).toBe('https://agent.example/alice/x.docx');
    expect(calls[0].path).toBe(
      'artifacts/download-url?key=alice%2Fproj_1%2Fartifacts%2Fart_1%2Fx.docx',
    );
  });

  it('passes a refusal (e.g. 403 for another user’s key) to the caller', async () => {
    const fetchApi = (async () => {
      throw new Error('API error: 403');
    }) as FetchApi;

    await expect(
      requestArtifactDownloadUrl(fetchApi, 'bob/proj_1/artifacts/art_1/x.docx'),
    ).rejects.toThrow('API error: 403');
  });
});

describe('upload content type', () => {
  it('prefers the type the browser reports', () => {
    expect(uploadContentType({ name: 'a.pdf', type: 'application/pdf' })).toBe(
      'application/pdf',
    );
  });

  it('falls back to the extension, then to application/octet-stream', () => {
    expect(uploadContentType({ name: 'ledger.CSV', type: '' })).toBe(
      'text/csv',
    );
    expect(uploadContentType({ name: 'plan.dxf', type: '' })).toBe(
      'application/dxf',
    );
    expect(uploadContentType({ name: 'notes.md', type: '' })).toBe(
      'application/octet-stream',
    );
    expect(getMimeTypeByExt('no-extension')).toBe('application/octet-stream');
  });
});

describe('putToPresignedUrl', () => {
  it('PUTs the file with the declared content type and no credentials', async () => {
    const file = new File(['%PDF-1.7 synthetic'], 'bank_statement.pdf', {
      type: 'application/pdf',
    });
    const fetchImpl = vi.fn(async () => new Response(null, { status: 200 }));

    await putToPresignedUrl(
      PRESIGNED.url,
      file,
      fetchImpl as unknown as typeof fetch,
    );

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(PRESIGNED.url);
    expect(init.method).toBe('PUT');
    expect(init.body).toBe(file);
    // Same value as declared to the backend: Content-Type is a signed header.
    expect(init.headers).toEqual({ 'Content-Type': uploadContentType(file) });
    expect(init.credentials).toBeUndefined();
  });

  it('uses the extension fallback when the browser reports no type', async () => {
    const file = new File(['a,b\n1,2\n'], 'ledger.csv');
    const fetchImpl = vi.fn(async () => new Response(null, { status: 200 }));

    await putToPresignedUrl(
      PRESIGNED.url,
      file,
      fetchImpl as unknown as typeof fetch,
    );

    const init = (
      fetchImpl.mock.calls[0] as unknown as [string, RequestInit]
    )[1];
    expect(init.headers).toEqual({ 'Content-Type': 'text/csv' });
  });

  it('throws when S3 refuses the upload (expired URL, other size or type)', async () => {
    const file = new File(['x'], 'a.pdf', { type: 'application/pdf' });
    const fetchImpl = vi.fn(async () => new Response(null, { status: 403 }));

    await expect(
      putToPresignedUrl(
        PRESIGNED.url,
        file,
        fetchImpl as unknown as typeof fetch,
      ),
    ).rejects.toThrow('Failed to upload a.pdf to S3');
  });
});
