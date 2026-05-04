/**
 * list-leads.js, `npm run leads` helper.
 *
 * Prints the most recent submissions from the `leads` table without
 * needing a psql install. Reads DATABASE_URL from the environment, so
 * `railway run npm run leads` works against the deployed database.
 *
 * Default limit: 50 most recent rows. Override with `--limit=N`.
 */
import pg from 'pg';

const DATABASE_URL = process.env.DATABASE_URL;
if (!DATABASE_URL) {
  console.error('DATABASE_URL not set. Try: railway run npm run leads');
  process.exit(1);
}

const limitArg = process.argv.find((a) => a.startsWith('--limit='));
const LIMIT = Math.max(1, Math.min(500, Number((limitArg || '').split('=')[1]) || 50));

const pool = new pg.Pool({
  connectionString: DATABASE_URL,
  ssl: process.env.DB_SSL === 'true' ? { rejectUnauthorized: false } : false,
});

try {
  const { rows } = await pool.query(
    `SELECT id, created_at, name, email, company, role, erp, topic,
            COALESCE(LEFT(message, 80), '') AS message_preview
       FROM leads
   ORDER BY created_at DESC
      LIMIT $1`,
    [LIMIT]
  );

  if (rows.length === 0) {
    console.log('(no leads yet)');
  } else {
    console.log(`# ${rows.length} most recent lead(s)\n`);
    for (const r of rows) {
      const ts = new Date(r.created_at).toISOString().replace('T', ' ').slice(0, 19);
      console.log(`#${r.id}  ${ts}  ${r.name} <${r.email}>`);
      const meta = [r.company, r.role, r.erp, r.topic].filter(Boolean).join(' · ');
      if (meta) console.log(`    ${meta}`);
      if (r.message_preview) console.log(`    "${r.message_preview}${r.message_preview.length === 80 ? '…' : ''}"`);
      console.log('');
    }
  }
} catch (err) {
  console.error('query failed:', err.message);
  process.exitCode = 1;
} finally {
  await pool.end();
}
