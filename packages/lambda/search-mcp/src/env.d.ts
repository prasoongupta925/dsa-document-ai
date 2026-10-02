declare global {
  namespace NodeJS {
    interface ProcessEnv {
      LANCEDB_FUNCTION_ARN: string;
      GRAPH_SERVICE_FUNCTION_ARN: string;
      DOCUMENT_STORAGE_BUCKET: string;
    }
  }
}

export {};
