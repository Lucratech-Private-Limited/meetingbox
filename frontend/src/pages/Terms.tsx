import { Link } from 'react-router-dom'

const LAST_UPDATED = 'April 17, 2026'

export default function Terms() {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      <header className="border-b border-gray-800 px-4 py-4">
        <div className="mx-auto max-w-3xl flex items-center justify-between gap-4">
          <Link to="/login" className="text-sm font-medium text-blue-400 hover:text-blue-300">
            ← Back to sign in
          </Link>
          <span className="text-sm text-gray-500">MeetingBox AI</span>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-10 pb-16">
        <h1 className="text-3xl font-bold text-white tracking-tight mb-2">Terms of Service</h1>
        <p className="text-sm text-gray-500 mb-8">Last updated: {LAST_UPDATED}</p>

        <div className="space-y-6 text-sm leading-relaxed text-gray-300">
          <p className="rounded-lg border border-amber-900/50 bg-amber-950/30 px-4 py-3 text-amber-200/90">
            These terms are a practical template for OAuth listings and early customers. They are not
            legal advice. Have qualified counsel review and adapt them for your jurisdiction and
            product.
          </p>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">1. Agreement</h2>
            <p>
              By accessing or using MeetingBox AI operated by{' '}
              <strong className="text-gray-100">Lucra Tech Solutions</strong> (&quot;we&quot;,
              &quot;us&quot;), you agree to these Terms of Service. If you do not agree, do not use
              the service.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">2. The service</h2>
            <p>
              We provide meeting-related software features on an as-is basis. We may change, suspend,
              or discontinue features with reasonable notice when practicable.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">3. Accounts</h2>
            <p>
              You are responsible for activity under your account, for keeping credentials secure,
              and for ensuring that any organization you represent is authorized to use the service.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">4. Acceptable use</h2>
            <p>You agree not to misuse the service, including by:</p>
            <ul className="list-disc pl-5 space-y-2 mt-2">
              <li>Violating applicable law or third-party rights;</li>
              <li>Attempting unauthorized access to systems, data, or accounts;</li>
              <li>Uploading malware or disrupting the service or other users;</li>
              <li>Reverse engineering except where applicable law expressly permits.</li>
            </ul>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">5. Your content</h2>
            <p>
              You retain rights in content you submit. You grant us a limited license to host,
              process, and display that content solely to provide the service to you and as described in
              our{' '}
              <Link to="/privacy" className="text-blue-400 hover:text-blue-300 underline">
                Privacy Policy
              </Link>
              .
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">6. Third-party services</h2>
            <p>
              Optional integrations (such as Google sign-in or productivity tools you connect) are
              subject to those providers&apos; terms and policies in addition to ours.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">7. Disclaimers</h2>
            <p>
              THE SERVICE IS PROVIDED &quot;AS IS&quot; AND &quot;AS AVAILABLE&quot; WITHOUT WARRANTIES
              OF ANY KIND, WHETHER EXPRESS OR IMPLIED, INCLUDING MERCHANTABILITY, FITNESS FOR A
              PARTICULAR PURPOSE, AND NON-INFRINGEMENT, TO THE MAXIMUM EXTENT PERMITTED BY LAW.
              Automated summaries or AI outputs may be inaccurate; you should verify important
              information.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">8. Limitation of liability</h2>
            <p>
              TO THE MAXIMUM EXTENT PERMITTED BY LAW, WE WILL NOT BE LIABLE FOR ANY INDIRECT,
              INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR ANY LOSS OF PROFITS, DATA,
              OR GOODWILL, ARISING FROM YOUR USE OF THE SERVICE. OUR AGGREGATE LIABILITY FOR CLAIMS
              RELATING TO THE SERVICE WILL NOT EXCEED THE GREATER OF (A) THE AMOUNTS YOU PAID US FOR
              THE SERVICE IN THE TWELVE MONTHS BEFORE THE CLAIM OR (B) ONE HUNDRED U.S. DOLLARS (USD
              $100), UNLESS APPLICABLE LAW REQUIRES OTHERWISE.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">9. Indemnity</h2>
            <p>
              You will defend and indemnify us against claims arising from your content, your misuse
              of the service, or your violation of these terms, subject to applicable law.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">10. Termination</h2>
            <p>
              You may stop using the service at any time. We may suspend or terminate access for
              violations of these terms or to protect the service or other users.
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">11. Governing law</h2>
            <p>
              These terms are governed by the laws of the jurisdiction you designate with your counsel
              (placeholder: insert governing law and venue here).
            </p>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-white mb-2">12. Contact</h2>
            <p>
              Questions:{' '}
              <a
                href="mailto:legal@lucratechsol.com"
                className="text-blue-400 hover:text-blue-300 underline"
              >
                legal@lucratechsol.com
              </a>{' '}
              (replace with your operational legal or support inbox).
            </p>
          </section>

          <p className="pt-4 text-gray-500">
            See also our{' '}
            <Link to="/privacy" className="text-blue-400 hover:text-blue-300 underline">
              Privacy Policy
            </Link>
            .
          </p>
        </div>
      </main>
    </div>
  )
}
