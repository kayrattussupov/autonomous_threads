"use client";

export default function DashboardError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main style={{ padding: 16 }}>
      <h1>Ошибка</h1>
      <p>{error.message || "Что-то пошло не так."}</p>
      <button onClick={() => reset()}>Повторить</button>
    </main>
  );
}
