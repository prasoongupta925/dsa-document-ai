declare global {
  namespace NodeJS {
    interface ProcessEnv {
      WS_CONNECTIONS_TABLE_NAME: string;
      BACKEND_TABLE_NAME: string;
    }
  }
}

export {};
