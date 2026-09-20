import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { NotificationProvider } from "./components/ui/Notification";
import { AppShell } from "./shell/AppShell";
import { LoginPage } from "./pages/LoginPage";
import { PasswordResetRequestPage } from "./pages/PasswordResetRequestPage";
import { HomePage } from "./pages/HomePage";
import { PeoplePage } from "./pages/PeoplePage";
import { PersonDetailPage } from "./pages/PersonDetailPage";
import { GroupsPage } from "./pages/GroupsPage";
import { GroupDetailPage } from "./pages/GroupDetailPage";
import { EventsPage } from "./pages/EventsPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
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
              <Route path="people" element={<PeoplePage />} />
              <Route path="people/:personId" element={<PersonDetailPage />} />
              <Route path="groups" element={<GroupsPage />} />
              <Route path="groups/:groupId" element={<GroupDetailPage />} />
              <Route path="events" element={<EventsPage />} />
              <Route path="achievements" element={<PlaceholderPage title="Достижения" />} />
              <Route path="reports" element={<PlaceholderPage title="Отчёты" />} />
              <Route path="settings" element={<PlaceholderPage title="Настройки" />} />
              <Route path="*" element={<NotFoundPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </NotificationProvider>
    </QueryClientProvider>
  );
}
