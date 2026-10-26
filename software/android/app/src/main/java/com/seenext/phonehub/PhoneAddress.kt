package com.seenext.phonehub

import java.net.Inet4Address
import java.net.NetworkInterface

object PhoneAddress {
    fun ipv4(): String {
        val found = ArrayList<String>()
        val interfaces = NetworkInterface.getNetworkInterfaces() ?: return ""
        while (interfaces.hasMoreElements()) {
            val nif = interfaces.nextElement()
            if (!nif.isUp || nif.isLoopback) continue
            val addresses = nif.inetAddresses
            while (addresses.hasMoreElements()) {
                val addr = addresses.nextElement()
                if (addr is Inet4Address && !addr.isLoopbackAddress && !addr.isLinkLocalAddress) {
                    found.add(addr.hostAddress ?: continue)
                }
            }
        }
        return found.firstOrNull { it == "192.168.43.1" } ?: found.firstOrNull().orEmpty()
    }

    fun glassesPage(): String {
        val ip = ipv4()
        return if (ip.isEmpty()) "http://本机:8788/" else "http://$ip:8788/"
    }
}
