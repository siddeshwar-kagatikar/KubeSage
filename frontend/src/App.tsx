import { Route, Routes } from 'react-router-dom'
import Layout from './components/Layout'
import OverviewPage from './pages/OverviewPage'
import WorkloadPage from './pages/WorkloadPage'
import StartupPage from './pages/StartupPage'
import IncidentPage from './pages/IncidentPage'
import NotFoundPage from './pages/NotFoundPage'

/** Route table. Later phases (diagnosis, fixes, experiments) add routes here and an entry in NAV_ITEMS. */
export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<OverviewPage />} />
        <Route path="workloads/:id" element={<WorkloadPage />} />
        <Route path="startups/:id" element={<StartupPage />} />
        <Route path="incidents/:id" element={<IncidentPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
