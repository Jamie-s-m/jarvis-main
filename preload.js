const { contextBridge } = require('electron')

// Expose a minimal API surface for future native integrations
contextBridge.exposeInMainWorld('jarvisNative', {
  ping: () => 'pong'
})
