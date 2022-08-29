import unittest

from systemd_ctypes import introspection


class TestIntrospection(unittest.TestCase):

    def test_annotated_signal(self):
        xml = """
<node>
  <interface name="org.freedesktop.DBus.Introspectable">
    <signal name="Details">
          <annotation name="org.qtproject.QtDBus.QtTypeName.Out0" value="QVariantMap">
          </annotation>
          <arg type="a{sv}" name="data">
          </arg>
        </signal>
  </interface>
</node>
"""
        parsed = introspection.parse_xml(xml)
        interface = 'org.freedesktop.DBus.Introspectable'
        self.assertEqual(parsed[interface]['methods'], {})
        self.assertEqual(parsed[interface]['properties'], {})
        self.assertEqual(parsed[interface]['signals'], {'Details': {'in': ['a{sv}']}})

    def test_empty_signal(self):
        xml = """
<node>
  <interface name="org.freedesktop.DBus.Introspectable">
    <signal name="Details">
    </signal>
  </interface>
</node>
"""
        parsed = introspection.parse_xml(xml)
        interface = 'org.freedesktop.DBus.Introspectable'
        self.assertEqual(parsed[interface]['methods'], {})
        self.assertEqual(parsed[interface]['properties'], {})
        self.assertEqual(parsed[interface]['signals'], {'Details': {'in': []}})

    def test_signal(self):
        xml = """
<node>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Set">
      <arg type="s" name="interface_name" direction="in"/>
      <arg type="s" name="property_name" direction="in"/>
      <arg type="v" name="value"/>
    </method>
    <signal name="PropertiesChanged">
      <arg type="s" name="interface_name"/>
      <arg type="a{sv}" name="changed_properties"/>
      <arg type="as" name="invalidated_properties"/>
    </signal>
  </interface>
</node>
"""
        parsed = introspection.parse_xml(xml)
        interface = 'org.freedesktop.DBus.Properties'
        self.assertEqual(parsed[interface]['methods'], {'Set': {'in': ['s', 's', 'v'], 'out': []}})
        self.assertEqual(parsed[interface]['properties'], {})
        self.assertEqual(parsed[interface]['signals'], {'PropertiesChanged': {'in': ['s', 'a{sv}', 'as']}})
