export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const params = await searchParams;

  return (
    <main style={{ maxWidth: 320, margin: "80px auto", fontFamily: "sans-serif" }}>
      <h1>Threads Agent Dashboard</h1>
      <form method="POST" action="/api/login">
        <input type="hidden" name="next" value={params.next ?? "/"} />
        <label htmlFor="password">Пароль</label>
        <input
          id="password"
          name="password"
          type="password"
          autoFocus
          style={{ display: "block", width: "100%", marginTop: 8, marginBottom: 12 }}
        />
        <button type="submit">Войти</button>
      </form>
      {params.error && <p style={{ color: "crimson" }}>Неверный пароль.</p>}
    </main>
  );
}
