using System;
using System.IO;
using System.Linq;
using System.Reflection;

class P
{
    static string GameDir = @"D:\steam\steamapps\common\Stardew Valley";
    static void Main(string[] args)
    {
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            var name = new AssemblyName(e.Name).Name;
            var p = Path.Combine(GameDir, name + ".dll");
            return File.Exists(p) ? Assembly.LoadFrom(p) : null;
        };
        var asm = Assembly.LoadFrom(Path.Combine(GameDir, "Stardew Valley.dll"));
        foreach (var q in args)
        {
            var parts = q.Split('|');
            var t = asm.GetTypes().FirstOrDefault(x => x.FullName == parts[0]) ?? asm.GetTypes().FirstOrDefault(x => x.Name == parts[0] && !x.IsNested);
            if (t == null) { Console.WriteLine($"TYPE NOT FOUND: {parts[0]}"); continue; }
            var filter = parts.Length > 1 ? parts[1] : "";
            Console.WriteLine($"== {t.FullName} ({filter})");
            foreach (var c in t.GetConstructors(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance))
                Console.WriteLine("  C (" + string.Join(", ", c.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name)) + ")");
            foreach (var m in t.GetMembers(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static)
                               .Where(m => m.Name.IndexOf(filter, StringComparison.OrdinalIgnoreCase) >= 0))
            {
                if (m is MethodInfo mi)
                    Console.WriteLine((mi.IsStatic ? "  S " : "  I ") + mi.ReturnType.Name + " " + mi.Name + "(" + string.Join(", ", mi.GetParameters().Select(p => p.ParameterType.Name + " " + p.Name)) + ")");
                else if (m is FieldInfo fi) Console.WriteLine((fi.IsStatic ? "  SF " : "  IF ") + fi.FieldType.Name + " " + fi.Name);
                else if (m is PropertyInfo pi) Console.WriteLine("  P " + pi.PropertyType.Name + " " + pi.Name);
            }
        }
    }
}

