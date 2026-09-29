import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { NotificationProvider } from "./components/ui/Notification";
import { AppShell } from "./shell/AppShell";
import { SectionGuard } from "./shell/SectionGuard";
import { LoginPage } from "./pages/LoginPage";
import { PasswordResetRequestPage } from "./pages/PasswordResetRequestPage";
import { HomePage } from "./pages/HomePage";
import { PeoplePage } from "./pages/PeoplePage";
import { PersonDetailPage } from "./pages/PersonDetailPage";
import { GroupsPage } from "./pages/GroupsPage";
import { GroupDetailPage } from "./pages/GroupDetailPage";
import { EventsPage } from "./pages/EventsPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { SettingsPage } from "./pages/SettingsPage";
import { NotFoundPage } from "./pages/NotFoundPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <NotificationProvider>
        <BrowserRouter
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <Routes>
            <Route path="/login" element={<LoginPage />} />
            <Route path="/password-reset" element={<PasswordResetRequestPage />} />
            <Route element={<AppShell />}>
              <Route index element={<HomePage />} />
              {/* Issue #212: sections hidden from the user's role-aware
                  navigation render a 403 state on direct visit (UX only;
                  backend authorization stays authoritative). */}
              <Route element={<SectionGuard section="people" />}>
                <Route path="people" element={<PeoplePage />} />
                <Route path="people/:personId" element={<PersonDetailPage />} />
              </Route>
              <Route element={<SectionGuard section="groups" />}>
                <Route path="groups" element={<GroupsPage />} />
                <Route path="groups/:groupId" element={<GroupDetailPage />} />
              </Route>
              <Route element={<SectionGuard section="events" />}>
                <Route path="events" element={<EventsPage />} />
              </Route>
              <Route element={<SectionGuard section="achievements" />}>
                <Route path="achievements" element={<PlaceholderPage title="Достижения" />} />
              </Route>
              <Route element={<SectionGuard section="reports" />}>
                <Route path="reports" element={<PlaceholderPage title="Отчёты" />} />
              </Route>
              <Route element={<SectionGuard section="settings" />}>
                <Route path="settings" element={<SettingsPage />} />
              </Route>
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </NotificationProvider>
    </QueryClientProvider>
  );
}
