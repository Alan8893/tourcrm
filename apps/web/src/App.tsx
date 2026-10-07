import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import { NotificationProvider } from "./components/ui/Notification";
import { AppShell } from "./shell/AppShell";
import { SectionGuard } from "./shell/SectionGuard";
import { GroupDetailGuard } from "./shell/GroupDetailGuard";
import { AdministratorGuard } from "./shell/AdministratorGuard";
import { LoginPage } from "./pages/LoginPage";
import { PasswordResetRequestPage } from "./pages/PasswordResetRequestPage";
import { HomePage } from "./pages/HomePage";
import { PeoplePage } from "./pages/PeoplePage";
import { PersonDetailPage } from "./pages/PersonDetailPage";
import { GroupsPage } from "./pages/GroupsPage";
import { GroupDetailPage } from "./pages/GroupDetailPage";
import { EventsPage } from "./pages/EventsPage";
import { AchievementsPage } from "./pages/AchievementsPage";
import { AchievementDefinitionPage } from "./pages/AchievementDefinitionPage";
import { NormativeSetPage } from "./pages/NormativeSetPage";
import { ReportsPage } from "./pages/ReportsPage";
import { ExportPage } from "./pages/ExportPage";
import { EventParticipantsReportPage } from "./pages/EventParticipantsReportPage";
import { ImportPage } from "./pages/ImportPage";
import { SettingsPage } from "./pages/SettingsPage";
import { InventoryPage } from "./pages/InventoryPage";
import { InventoryItemPage } from "./pages/InventoryItemPage";
import { InventoryInstancePage } from "./pages/InventoryInstancePage";
import { InventoryIssuePage } from "./pages/InventoryIssuePage";
import { NewsListPage } from "./pages/NewsListPage";
import { NewsDetailPage } from "./pages/NewsDetailPage";
import { NewsManagePage } from "./pages/NewsManagePage";
import { NewsFormPage } from "./pages/NewsFormPage";
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
                {/* TH-0118.5: «Люди → Импорт» — Administrator-only inside a
                    section Instructor also sees, hence the nested guard. */}
                <Route element={<AdministratorGuard />}>
                  <Route path="people/import" element={<ImportPage />} />
                </Route>
                <Route path="people/:personId" element={<PersonDetailPage />} />
              </Route>
              <Route element={<SectionGuard section="groups" />}>
                <Route path="groups" element={<GroupsPage />} />
              </Route>
              {/* ADR-0046 / Issue #301: Group Detail is also reached
                  contextually by Guardian (Home → «Мои дети» → Group),
                  without a Groups navigation section. */}
              <Route element={<GroupDetailGuard />}>
                <Route path="groups/:groupId" element={<GroupDetailPage />} />
              </Route>
              <Route element={<SectionGuard section="events" />}>
                <Route path="events" element={<EventsPage />} />
              </Route>
              <Route element={<SectionGuard section="achievements" />}>
                {/* Issue #220: Administrator sees the Achievements
                    administration; other roles keep the placeholder. The
                    detail pages are Administrator-only (UX guard; backend
                    authoritative). */}
                <Route path="achievements" element={<AchievementsPage />} />
                <Route element={<AdministratorGuard />}>
                  <Route path="achievements/definitions/:definitionId" element={<AchievementDefinitionPage />} />
                  <Route path="achievements/normative-sets/:setId" element={<NormativeSetPage />} />
                </Route>
              </Route>
              <Route element={<SectionGuard section="reports" />}>
                <Route path="reports" element={<ReportsPage />} />
                {/* TH-0118.5: «Отчёты → Экспорт»; Issue #299: «Отчёты →
                    Участники мероприятий» (Administrator-only section). */}
                <Route element={<AdministratorGuard />}>
                  <Route path="reports/export" element={<ExportPage />} />
                  <Route path="reports/event-participants" element={<EventParticipantsReportPage />} />
                </Route>
              </Route>
              {/* TH-0120 / Issue #227: News is reachable from Home («Все
                  новости») for all four roles — not a navigation section,
                  so no SectionGuard. Management is a contextual,
                  Administrator-only page (UX guard; backend authoritative). */}
              <Route path="news" element={<NewsListPage />} />
              <Route element={<AdministratorGuard />}>
                <Route path="news/manage" element={<NewsManagePage />} />
                <Route path="news/manage/new" element={<NewsFormPage />} />
                <Route path="news/manage/:newsId/edit" element={<NewsFormPage />} />
              </Route>
              <Route path="news/:newsId" element={<NewsDetailPage />} />
              {/* Issue #230: «Склад» is Administrator-only (docs/04-domain/
                  inventory.md §3–4; UX guard, backend authoritative). */}
              <Route element={<SectionGuard section="inventory" />}>
                <Route path="inventory" element={<InventoryPage />} />
                <Route path="inventory/items/:itemId" element={<InventoryItemPage />} />
                <Route path="inventory/instances/:instanceId" element={<InventoryInstancePage />} />
                <Route path="inventory/issues/:issueId" element={<InventoryIssuePage />} />
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
