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
  const [responseStatus, setResponseStatus] = useState(null)
  const [responseMeta, setResponseMeta] = useState(null)
  const [requestHistory, setRequestHistory] = useState([])
  const [isSending, setIsSending] = useState(false)
  const [jsonError, setJsonError] = useState('')

  const clearPlayground = () => {
    setSelectedEndpoint('')
    setRequestBody(
      JSON.stringify(
        {
          example: "value"
        },
        null,
        2
      )
    )

    setResponseData(null)
    setResponseStatus(null)
    setResponseMeta(null)
    setJsonError('')
    setRequestHistory([])
  }

  const copyResponse = async () => {
    if (!responseData) return

    await navigator.clipboard.writeText(
      JSON.stringify(responseData, null, 2)
    )
  }

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

      setResponseStatus(response.status)

      const data = await response.json()

      setResponseData(data)

      setResponseMeta({
        timestamp: new Date().toLocaleTimeString(),
        size: JSON.stringify(data).length
      })

      setRequestHistory(prev => [
        {
          endpoint: selectedEndpoint,
          timestamp: new Date().toLocaleTimeString()
        },
        ...prev
      ])
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

                <button
                  onClick={clearPlayground}
                  className="bg-red-600 hover:bg-red-700 text-white px-3 py-2 rounded-lg text-sm"
                >
                  🗑 Clear
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

                  {responseStatus && (
                    <div className="mb-3">
                      <span
                        className={`px-3 py-1 rounded-full text-sm font-semibold ${
                          responseStatus >= 200 && responseStatus < 300
                            ? 'bg-emerald-600 text-white'
                            : responseStatus >= 400
                            ? 'bg-red-600 text-white'
                            : 'bg-yellow-600 text-white'
                        }`}
                      >
                        HTTP {responseStatus}
                      </span>
                    </div>
                  )}

                  <div className="flex items-center justify-between mb-2">
                    <label className="block text-slate-300 text-sm">
                      Response
                    </label>

                    <button
                      onClick={copyResponse}
                      className="bg-slate-700 hover:bg-slate-600 text-white text-xs px-3 py-1 rounded"
                    >
                      📋 Copy Response
                    </button>
                  </div>

                  {responseMeta && (
                    <div className="bg-slate-900 border border-slate-700 rounded-lg p-3 mb-3">
                      <div className="text-sm text-slate-300">
                        📏 Response Size: {responseMeta.size} bytes
                      </div>

                      <div className="text-sm text-slate-300 mt-1">
                        🕒 Received: {responseMeta.timestamp}
                      </div>
                    </div>
                  )}

                  <pre className="bg-slate-900 border border-slate-700 rounded-lg p-4 text-slate-300 text-sm overflow-auto">
                    {JSON.stringify(responseData, null, 2)}
                  </pre>
                </div>
              )}

              {requestHistory.length > 0 && (
                <div className="mt-6">
                  <h3 className="text-slate-300 font-semibold mb-3">
                    📜 Request History
                  </h3>

                  <div className="space-y-2">
                    {requestHistory.map((item, index) => (
                      <div
                        key={index}
                        className="bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 flex justify-between items-center"
                      >
                        <span className="text-emerald-400 font-mono">
                          POST {item.endpoint}
                        </span>

                        <span className="text-xs text-slate-500">
                          {item.timestamp}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}