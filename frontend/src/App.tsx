import { Route, Routes } from "react-router";
import { RequireAuth } from "@/auth";
import { Layout } from "@/components/Layout";
import { LoginPage } from "@/pages/LoginPage";

const BrowsePage = () => <div className="p-8">Browse</div>;
const DocumentPage = () => <div className="p-8">Document</div>;
const ScanPage = () => <div className="p-8">Scan</div>;
const SearchPage = () => <div className="p-8">Search</div>;
const ChatPage = () => <div className="p-8">Chat</div>;

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route element={<RequireAuth />}>
        <Route element={<Layout />}>
          <Route path="/" element={<BrowsePage />} />
          <Route path="/documents/:id" element={<DocumentPage />} />
          <Route path="/scan" element={<ScanPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/chat" element={<ChatPage />} />
        </Route>
      </Route>
    </Routes>
  );
}
