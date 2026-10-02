declare namespace NodeJS {
  interface ProcessEnv {
    WS_CONNECTIONS_TABLE_NAME: string;
    WEBSOCKET_CALLBACK_URL: string;
    BACKEND_TABLE_NAME: string;
  }
}
