export interface ArtifactKeyInfo {
  userId: string;
  projectId: string;
  artifactId: string;
  filename: string;
}

/**
 * Parse S3 key for artifact files.
 * Pattern: {user_id}/{project_id}/artifacts/{artifact_id}/{filename}
 * Example: drskur/proj_xxxxx/artifacts/art_xxxx/파일명.pptx
 */
export function parseArtifactS3Key(key: string): ArtifactKeyInfo | null {
  const parts = key.split('/');

  // 최소 5개 파트 필요: userId, projectId, "artifacts", artifactId, filename
  if (parts.length < 5) {
    return null;
  }

  const artifactsIndex = parts.indexOf('artifacts');
  if (artifactsIndex < 2 || artifactsIndex + 2 >= parts.length) {
    return null;
  }

  const userId = parts.slice(0, artifactsIndex - 1).join('/');
  const projectId = parts[artifactsIndex - 1];
  const artifactId = parts[artifactsIndex + 1];
  const filename = parts.slice(artifactsIndex + 2).join('/');

  if (!projectId.startsWith('proj_') || !artifactId.startsWith('art_')) {
    return null;
  }

  return {
    userId,
    projectId,
    artifactId,
    filename,
  };
}
