import { BrowserRouter, Routes, Route } from "react-router-dom";
import { StatusProvider } from "./state/status";
import Layout from "./components/Layout";
import DashboardPage from "./pages/DashboardPage";
import PlaylistsPage from "./pages/PlaylistsPage";
import PlaylistDetailPage from "./pages/PlaylistDetailPage";
import LoginPage from "./pages/LoginPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          element={
            <StatusProvider>
              <Layout />
            </StatusProvider>
          }
        >
          <Route path="/" element={<DashboardPage />} />
          <Route path="/playlists" element={<PlaylistsPage />} />
          <Route path="/playlists/:name" element={<PlaylistDetailPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
