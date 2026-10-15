export type ApiErrorCode = "network" | "http" | "invalid_response" | "unauthorized" | "not_configured" | "validation";

export class ApiError extends Error {
  constructor(
    public readonly code: ApiErrorCode,
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}
