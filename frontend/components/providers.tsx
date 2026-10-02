"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState } from "react";

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: 1 } } }),
  );
  useEffect(() => {
    // Маркер гидратации: e2e-тесты ждут его, прежде чем вводить данные в формы.
    document.body.dataset.hydrated = "true";
  }, []);
  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}
