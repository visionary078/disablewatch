package com.seenext.phonehub

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.TextView

class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        val gateway = findViewById<EditText>(R.id.gateway)
        val token = findViewById<EditText>(R.id.token)
        val toggle = findViewById<Button>(R.id.toggle)
        val status = findViewById<TextView>(R.id.status)
        gateway.setText(prefs.getString(HubService.EXTRA_GATEWAY, "https://watchapi.divesee.com"))
        token.setText(prefs.getString(HubService.EXTRA_TOKEN, ""))
        toggle.setOnClickListener {
            if (Build.VERSION.SDK_INT >= 33 &&
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            ) {
                requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
            }
            prefs.edit()
                .putString(HubService.EXTRA_GATEWAY, gateway.text.toString().trim())
                .putString(HubService.EXTRA_TOKEN, token.text.toString().trim())
                .apply()
            val intent = Intent(this, HubService::class.java)
                .putExtra(HubService.EXTRA_GATEWAY, gateway.text.toString().trim())
                .putExtra(HubService.EXTRA_TOKEN, token.text.toString().trim())
            if (Build.VERSION.SDK_INT >= 26) {
                startForegroundService(intent)
            } else {
                startService(intent)
            }
            status.text = "正在接收。眼镜打开 http://192.168.43.1:8788/"
            toggle.text = "已在接收"
        }
    }

    companion object {
        private const val PREFS = "phone-hub"
    }
}
