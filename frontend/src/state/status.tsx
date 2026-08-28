import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { getStatus, type StatusResponse } from "../api/bot";

interface StatusContextValue {
  status: StatusResponse | null;
  botName: string | null;
  error: string | null;
  loading: boolean;
  refresh: () => void;
}

const StatusContext = createContext<StatusContextValue | null>(null);

export function StatusProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    getStatus()
      .then((res) => {
        setStatus(res);
        setError(null);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <StatusContext.Provider
      value={{ status, botName: status?.bot ?? null, error, loading, refresh }}
    >
      {children}
    </StatusContext.Provider>
  );
}

export function useStatus(): StatusContextValue {
  const ctx = useContext(StatusContext);
  if (!ctx) throw new Error("useStatus must be used within StatusProvider");
  return ctx;
}
