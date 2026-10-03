import { createBrowserRouter, RouterProvider } from "react-router";
import { RequireAuth } from "@/auth";
import { Layout } from "@/components/Layout";
import { LoginPage } from "@/pages/LoginPage";
import { BrowsePage } from "@/pages/BrowsePage";
import { DocumentPage } from "@/pages/DocumentPage";
import { ScanPage } from "@/pages/ScanPage";
import { SearchPage } from "@/pages/SearchPage";

// Data router: required by useBlocker (scan page leave guard).
const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <Layout />,
        children: [
          { path: "/", element: <BrowsePage /> },
          { path: "/documents/:id", element: <DocumentPage /> },
          { path: "/scan", element: <ScanPage /> },
          { path: "/search", element: <SearchPage /> },
          { path: "/chat", lazy: () => import("@/pages/ChatPage").then((m) => ({ Component: m.ChatPage })) },
        ],
      },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
