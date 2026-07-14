import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { AuthProvider } from "@/auth";
import { clearToken, getToken } from "@/lib/api";
import { LoginPage } from "./LoginPage";

const fetchMock = vi.fn();
beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  clearToken();
});
afterEach(() => vi.unstubAllGlobals());

function json(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

it("logs in and stores the token", async () => {
  fetchMock.mockImplementation(async (url: string) => {
    if (url === "/api/auth/login") return json(200, { access_token: "tok42", token_type: "bearer" });
    if (url === "/api/auth/me") return json(200, { id: 1, username: "marco" });
    return json(404, { error: { code: "not_found", message: "no" } });
  });

  render(
    <MemoryRouter>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>,
  );
  await userEvent.type(screen.getByLabelText(/username/i), "marco");
  await userEvent.type(screen.getByLabelText(/password/i), "secret");
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

  expect(await screen.findByText(/redirecting/i)).toBeInTheDocument();
  expect(getToken()).toBe("tok42");
});

it("shows the error message on bad credentials", async () => {
  fetchMock.mockResolvedValue(json(401, { error: { code: "invalid_credentials", message: "Wrong username or password" } }));

  render(
    <MemoryRouter>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>,
  );
  await userEvent.type(screen.getByLabelText(/username/i), "marco");
  await userEvent.type(screen.getByLabelText(/password/i), "wrong");
  await userEvent.click(screen.getByRole("button", { name: /sign in/i }));

  expect(await screen.findByText(/wrong username or password/i)).toBeInTheDocument();
  expect(getToken()).toBeNull();
});
