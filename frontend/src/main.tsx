import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, lazy } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import "./index.css";
import { AppShell } from "@/components/layout/AppShell";
import { ToastProvider } from "@/components/ui";
import { ApiError } from "@/lib/api";

const Overview = lazy(() => import("@/pages/Overview"));
const BookingCheck = lazy(() => import("@/pages/BookingCheck"));
const Lanes = lazy(() => import("@/pages/Lanes"));
const Vendors = lazy(() => import("@/pages/Vendors"));
const Trips = lazy(() => import("@/pages/Trips"));
const TripDetail = lazy(() => import("@/pages/TripDetail"));
const Hubs = lazy(() => import("@/pages/Hubs"));
const Ask = lazy(() => import("@/pages/Ask"));
const Prompts = lazy(() => import("@/pages/Prompts"));
const Audit = lazy(() => import("@/pages/Audit"));
const Models = lazy(() => import("@/pages/Models"));
const NotFound = lazy(() => import("@/pages/NotFound"));

const qc = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: (n, e) => !(e instanceof ApiError && e.status >= 400 && e.status < 500) && n < 1 } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<Overview />} />
              <Route path="booking" element={<BookingCheck />} />
              <Route path="lanes" element={<Lanes />} />
              <Route path="vendors" element={<Vendors />} />
              <Route path="vendors/:code" element={<Vendors />} />
              <Route path="trips" element={<Trips />} />
              <Route path="trips/:id" element={<TripDetail />} />
              <Route path="hubs" element={<Hubs />} />
              <Route path="ask" element={<Ask />} />
              <Route path="prompts" element={<Prompts />} />
              <Route path="audit" element={<Audit />} />
              <Route path="audit/:runId" element={<Audit />} />
              <Route path="models" element={<Models />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
