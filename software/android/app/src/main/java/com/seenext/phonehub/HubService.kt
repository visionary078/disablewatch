package com.seenext.phonehub

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder

class HubService : Service() {
    private var hub: HubServer? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val gateway = intent?.getStringExtra(EXTRA_GATEWAY).orEmpty().ifEmpty { "https://watchapi.divesee.com" }
        val token = intent?.getStringExtra(EXTRA_TOKEN).orEmpty()
        startInForeground("正在等摄像头把画面送来")
        if (hub == null) {
            hub = HubServer(assets, gateway, token, ::startInForeground).also { it.start() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        hub?.stop()
        hub = null
        super.onDestroy()
    }

    private fun startInForeground(text: String) {
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) {
            val channel = NotificationChannel(CHANNEL, "接收摄像头", NotificationManager.IMPORTANCE_LOW)
            manager.createNotificationChannel(channel)
        }
        val notification = Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .setContentTitle("看见下一步")
            .setContentText(text)
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    companion object {
        const val EXTRA_GATEWAY = "gateway"
        const val EXTRA_TOKEN = "token"
        private const val CHANNEL = "phone-hub"
        private const val NOTIFICATION_ID = 7
    }
}
