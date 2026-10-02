import { describe, it, expect } from 'vitest';
import { parseArtifactS3Key } from './parse-artifact-key';

describe('parseArtifactS3Key', () => {
  it('should parse standard artifact S3 key', () => {
    const key =
      'drskur/proj_aoGa4kFiCpVmy8MBslZUY/artifacts/art_7yEfeeHGIWBraxdDv1iIS/A_young_girl_drinking_warm.jpeg';

    const result = parseArtifactS3Key(key);

    expect(result).toEqual({
      userId: 'drskur',
      projectId: 'proj_aoGa4kFiCpVmy8MBslZUY',
      artifactId: 'art_7yEfeeHGIWBraxdDv1iIS',
      filename: 'A_young_girl_drinking_warm.jpeg',
    });
  });

  it('should parse pptx artifact S3 key correctly', () => {
    // 실제 저장된 s3_key
    const key =
      'drskur/proj_cnxQzigXN85_goVsOW05x/artifacts/art_uPReAg38JFO0/presentation.pptx';

    const result = parseArtifactS3Key(key);

    // 기대 결과: artifactId는 art_uPReAg38JFO0 이어야 함
    expect(result).toEqual({
      userId: 'drskur',
      projectId: 'proj_cnxQzigXN85_goVsOW05x',
      artifactId: 'art_uPReAg38JFO0',
      filename: 'presentation.pptx',
    });
  });

  it('should parse artifact key with Korean filename', () => {
    const key = 'drskur/proj_xxxxx/artifacts/art_xxxx/파일명.pptx';

    const result = parseArtifactS3Key(key);

    expect(result).toEqual({
      userId: 'drskur',
      projectId: 'proj_xxxxx',
      artifactId: 'art_xxxx',
      filename: '파일명.pptx',
    });
  });

  it('should parse artifact key with nested filename path', () => {
    const key =
      'drskur/proj_aoGa4kFiCpVmy8MBslZUY/artifacts/art_7yEfeeHGIWBraxdDv1iIS/subdir/file.pdf';

    const result = parseArtifactS3Key(key);

    expect(result).toEqual({
      userId: 'drskur',
      projectId: 'proj_aoGa4kFiCpVmy8MBslZUY',
      artifactId: 'art_7yEfeeHGIWBraxdDv1iIS',
      filename: 'subdir/file.pdf',
    });
  });

  it('should return null for non-artifact key', () => {
    const key = 'drskur/proj_xxxxx/sessions/session_xxxxx/file.json';

    const result = parseArtifactS3Key(key);

    expect(result).toBeNull();
  });

  it('should return null for invalid project id format', () => {
    const key = 'drskur/invalid_project/artifacts/art_xxxx/file.pdf';

    const result = parseArtifactS3Key(key);

    expect(result).toBeNull();
  });

  it('should return null for invalid artifact id format', () => {
    const key = 'drskur/proj_xxxxx/artifacts/invalid_artifact/file.pdf';

    const result = parseArtifactS3Key(key);

    expect(result).toBeNull();
  });
});
