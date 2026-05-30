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
            </div>
          </>
        )}
      </div>
    </div>
  )
}