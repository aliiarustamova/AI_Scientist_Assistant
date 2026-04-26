const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export async function askAssistant(question: string, context: unknown) {
  const res = await fetch(`${API_BASE}/assistant`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      question,
      context,
    }),
  });

  if (!res.ok) {
    throw new Error("Failed to fetch assistant response");
  }

  return res.json();
}