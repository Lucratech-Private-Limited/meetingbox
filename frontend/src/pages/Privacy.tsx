import { Link } from 'react-router-dom'

const LAST_UPDATED = 'April 17, 2026'

export default function Privacy() {
  return (
    <div className="min-h-screen bg-app-page text-app-ink-muted">
      <header className="border-b border-app-border-light px-4 py-4">
        <div className="mx-auto max-w-3xl flex items-center justify-between gap-4">
          <Link to="/login" className="text-sm font-medium text-blue-400 hover:text-blue-300">
            ← Back to sign in
          </Link>
          <span className="text-sm text-app-ink-subtle">MeetingBox AI</span>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-10 pb-16">
        <h1 className="text-3xl font-bold text-white tracking-tight mb-2">Privacy Policy</h1>
        <p className="text-sm text-app-ink-subtle mb-8">Last updated: {LAST_UPDATED}</p>

        <div className="space-y-6 text-sm leading-relaxed text-app-ink-muted">
          <p className="rounded-lg border border-amber-900/50 bg-amber-950/30 px-4 py-3 text-amber-200/90">
            This policy is provided as a starting point for your Google Cloud OAuth and compliance
            checklist. It is not legal advice. Have qualified counsel review it before production use.
          </p>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">1. Who we are</h2>
            <p>
              MeetingBox AI is operated by{' '}
              <strong className="text-app-ink">Lucra Tech Solutions</strong> (&quot;we&quot;,
              &quot;us&quot;). Contact:{' '}
              <a
                href="mailto:privacy@lucratechsol.com"
                className="text-blue-400 hover:text-blue-300 underline"
              >
                privacy@lucratechsol.com
              </a>
              . You may replace this address with your real privacy inbox.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">2. What this service does</h2>
            <p>
              MeetingBox AI provides meeting-related features such as recordings, transcripts,
              summaries, and dashboard tools connected to your account. Features may change over time.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">3. Information we collect</h2>
            <ul className="list-disc pl-5 space-y-2 mt-2">
              <li>
                <strong className="text-app-ink">Account data:</strong> Information from your sign-in
                provider (for example Google), such as name, email address, and profile identifiers
                needed to authenticate you.
              </li>
              <li>
                <strong className="text-app-ink">Meeting content:</strong> Audio, transcripts,
                summaries, notes, and related metadata you or your organization upload or generate in
                the product.
              </li>
              <li>
                <strong className="text-app-ink">Technical data:</strong> Logs, diagnostics, IP
                address, device/browser type, and timestamps used to secure and operate the service.
              </li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">4. How we use information</h2>
            <p>We use the information above to:</p>
            <ul className="list-disc pl-5 space-y-2 mt-2">
              <li>Provide, maintain, and improve the service;</li>
              <li>Authenticate users and prevent abuse;</li>
              <li>Process meeting content as you request (for example transcription or summaries);</li>
              <li>Comply with law and enforce our terms.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">5. AI and subprocessors</h2>
            <p>
              Some features may use third-party AI or infrastructure providers to process prompts,
              transcripts, or related text. Those providers process data under contractual safeguards
              appropriate to the service. Integrations you enable (such as Google Calendar or Gmail,
              when offered) are governed by your choices in the product and the respective
              provider&apos;s terms.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">6. Retention</h2>
            <p>
              We retain information for as long as your account is active and as needed to provide the
              service, comply with legal obligations, resolve disputes, and enforce agreements.
              Retention details may depend on your organization&apos;s settings where applicable.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">7. Security</h2>
            <p>
              We use reasonable technical and organizational measures designed to protect personal and
              meeting information. No method of transmission or storage is completely secure.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">8. Your rights</h2>
            <p>
              Depending on where you live, you may have rights to access, correct, delete, or export
              personal data, or to object to or restrict certain processing. Contact us at the email
              above to make a request. We may need to verify your identity before responding.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">9. International transfers</h2>
            <p>
              If you use the service from outside the country where servers are located, your
              information may be transferred and processed across borders. We take steps designed to
              ensure appropriate safeguards where required.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">10. Children</h2>
            <p>
              The service is not directed at children under 16 (or the minimum age required in your
              jurisdiction). We do not knowingly collect personal information from children.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">11. Changes</h2>
            <p>
              We may update this policy from time to time. We will post the updated version on this
              page and revise the &quot;Last updated&quot; date.
            </p>
          </section>

          <p className="pt-4 text-app-ink-subtle">
            See also our{' '}
            <Link to="/terms" className="text-blue-400 hover:text-blue-300 underline">
              Terms of Service
            </Link>
            .
          </p>
        </div>
      </main>
    </div>
  )
}
