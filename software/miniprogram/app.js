const config = require("./utils/config")

App({
  onLaunch() {
    this.globalData.settings = config.loadSettings()
  },
  globalData: {
    settings: null,
  },
})
