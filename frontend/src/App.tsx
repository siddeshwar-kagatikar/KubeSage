import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import OverviewPage from './pages/OverviewPage'
import WorkloadPage from './pages/WorkloadPage'
import StartupPage from './pages/StartupPage'
import IncidentPage from './pages/IncidentPage'
import NotFoundPage from './pages/NotFoundPage'
import PlanPage from './pages/PlanPage'
import ExperimentPage from './pages/ExperimentPage'
import ExperimentsPage from './pages/ExperimentsPage'
import AuditPage from './pages/AuditPage'
import LlmOpsPage from './pages/LlmOpsPage'

/** Route table. Later phases (diagnosis, fixes, experiments) add routes here and an entry in NAV_ITEMS. */
export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<OverviewPage />} />
        <Route path="workloads/:id" element={<WorkloadPage />} />
        <Route path="startups/:id" element={<StartupPage />} />
        <Route path="incidents/:id" element={<IncidentPage />} />
        <Route path="plans/:id" element={<PlanPage />} />
        <Route path="experiments" element={<ExperimentsPage />} />
        <Route path="experiments/:id" element={<ExperimentPage />} />
        <Route path="audit" element={<AuditPage />} />
        <Route path="llmops" element={<LlmOpsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
