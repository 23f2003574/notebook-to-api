import { useState } from 'react'

export default function EndpointTester({ endpoints = [] }) {
  const [selectedEndpoint, setSelectedEndpoint] = useState('')
  const [requestBody, setRequestBody] = useState(
    JSON.stringify(
      {
        example: "value"
      },
      null,
      2
    )
  )

  const [responseData, setResponseData] = useState(null)
  const [isSending, setIsSending] = useState(false)
  const [jsonError, setJsonError] = useState('')

  const formatJson = () => {
    try {
      const parsed = JSON.parse(requestBody)

      setRequestBody(
        JSON.stringify(parsed, null, 2)
      )

      setJsonError('')
    } catch {
      setJsonError('Cannot format invalid JSON')
    }
  }

  const sendRequest = async () => {
    try {
      JSON.parse(requestBody)
      setJsonError('')
    } catch {
      setJsonError('Invalid JSON format')
      return
    }
    try {
      setIsSending(true)

      const response = await fetch(
        `http://localhost:8000${selectedEndpoint}`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: requestBody
        }
      )

      const data = await response.json()

      setResponseData(data)
    } catch (error) {
      setResponseData({
        error: error.message
      })
    } finally {
      setIsSending(false)
    }
  }

  return (
    <div className="bg-slate-800 rounded-lg border border-slate-700 p-6">
      <h2 className="text-xl font-semibold text-white mb-4">
        🧪 API Playground
      </h2>

      <p className="text-slate-400 mb-4">
        Test generated API endpoints directly from the dashboard.
      </p>
      <div className="space-y-4">
        <div>
          <label className="block text-slate-300 text-sm mb-2">
            Select Endpoint
          </label>

          <select
            value={selectedEndpoint}
            onChange={(e) => setSelectedEndpoint(e.target.value)}
            className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white"
          >
            <option value="">
              Choose an endpoint...
            </option>

            {endpoints.map((endpoint) => (
              <option key={endpoint} value={endpoint}>
                {endpoint}
              </option>
            ))}
          </select>
        </div>
        {selectedEndpoint && (
          <>
            <div className="bg-slate-900 border border-slate-700 rounded-lg p-4">
              <p className="text-emerald-400 font-mono">
                Selected: {selectedEndpoint}
              </p>
            </div>

            <div>
              <label className="block text-slate-300 text-sm mb-2">
                Request Body
              </label>
              <textarea
                value={requestBody}
                onChange={(e) => setRequestBody(e.target.value)}
                rows={10}
                className="w-full bg-slate-900 border border-slate-700 rounded-lg p-3 text-white font-mono text-sm"
              />
              <div className="flex gap-2 mt-3">
                <button
                  onClick={formatJson}
                  className="bg-slate-700 hover:bg-slate-600 text-white px-3 py-2 rounded-lg text-sm"
                >
                  ✨ Format JSON
                </button>
              </div>
              {jsonError && (
                <p className="text-red-400 text-sm mt-2">
                  ❌ {jsonError}
                </p>
              )}
              <button
                onClick={sendRequest}
                disabled={isSending}
                className="mt-4 bg-emerald-600 hover:bg-emerald-700 disabled:bg-slate-600 text-white px-4 py-2 rounded-lg"
              >
                {isSending ? 'Sending...' : 'Send Request'}
              </button>

              {responseData && (
                <div className="mt-4">
                  <label className="block text-slate-300 text-sm mb-2">
                    Response
                  </label>

                  <pre className="bg-slate-900 border border-slate-700 rounded-lg p-4 text-slate-300 text-sm overflow-auto">
                    {JSON.stringify(responseData, null, 2)}
                  </pre>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}