using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows;
using System.Windows.Automation;

namespace UiaScan
{
    class ElementInfo
    {
        public int Id;
        public string Role;
        public string Name;
        public string Value;
        public string AutomationId;
        public int Left, Top, Right, Bottom;
        public int Cx, Cy;
        public int Nx, Ny, Nw, Nh, Ncx, Ncy;
        public bool Enabled;
    }

    class Program
    {
        [DllImport("user32.dll")]
        static extern IntPtr GetForegroundWindow();

        [DllImport("user32.dll")]
        static extern bool SetProcessDpiAwarenessContext(IntPtr value);

        [DllImport("user32.dll")]
        static extern bool SetProcessDPIAware();

        [DllImport("user32.dll")]
        static extern int GetSystemMetrics(int nIndex);

        [DllImport("user32.dll")]
        static extern bool IsWindow(IntPtr hWnd);

        static readonly HashSet<string> InteractiveRoles = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "Button", "Edit", "Document", "Hyperlink", "MenuItem", "TabItem",
            "ListItem", "CheckBox", "RadioButton", "ComboBox", "Slider",
            "TreeItem", "DataItem", "SplitButton", "Spinner", "Thumb", "TitleBar"
        };

        static readonly HashSet<string> ContainerRoles = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            "Window", "Pane", "Group", "ToolBar", "MenuBar", "Menu", "Tab",
            "List", "Tree", "Table", "DataGrid", "Custom", "Header", "StatusBar",
            "ScrollBar", "Document", "TitleBar"
        };

        static string EscapeJson(string s)
        {
            if (string.IsNullOrEmpty(s)) return "";
            var sb = new StringBuilder(s.Length + 8);
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 0x20)
                            sb.AppendFormat("\\u{0:x4}", (int)c);
                        else
                            sb.Append(c);
                        break;
                }
            }
            return sb.ToString();
        }

        static string CleanText(string s, int maxLen = 80)
        {
            if (string.IsNullOrEmpty(s)) return "";
            s = s.Replace("\r", " ").Replace("\n", " ").Replace("\t", " ").Trim();
            while (s.Contains("  ")) s = s.Replace("  ", " ");
            if (s.Length > maxLen) s = s.Substring(0, maxLen) + "...";
            return s;
        }

        static void Main(string[] args)
        {
            Console.OutputEncoding = Encoding.UTF8;
            try { SetProcessDpiAwarenessContext(new IntPtr(-4)); }
            catch { try { SetProcessDPIAware(); } catch { } }

            IntPtr targetHwnd = IntPtr.Zero;
            bool includeDesktop = false;
            int maxElements = 80;
            int maxDepth = 10;
            int timeoutMs = 2500;
            bool includeText = false;

            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--hwnd" && i + 1 < args.Length)
                {
                    long h;
                    if (long.TryParse(args[++i], out h)) targetHwnd = new IntPtr(h);
                }
                else if (args[i] == "--desktop") includeDesktop = true;
                else if (args[i] == "--max" && i + 1 < args.Length) int.TryParse(args[++i], out maxElements);
                else if (args[i] == "--depth" && i + 1 < args.Length) int.TryParse(args[++i], out maxDepth);
                else if (args[i] == "--timeout" && i + 1 < args.Length) int.TryParse(args[++i], out timeoutMs);
                else if (args[i] == "--text") includeText = true;
            }

            int screenW = Math.Max(1, GetSystemMetrics(0));
            int screenH = Math.Max(1, GetSystemMetrics(1));

            var sw = Stopwatch.StartNew();
            var results = new List<ElementInfo>();
            var seenRects = new HashSet<string>();

            AutomationElement root = null;
            string rootName = "";

            try
            {
                if (includeDesktop)
                {
                    root = AutomationElement.RootElement;
                    rootName = "Desktop";
                }
                else
                {
                    if (targetHwnd == IntPtr.Zero || !IsWindow(targetHwnd))
                        targetHwnd = GetForegroundWindow();

                    if (targetHwnd != IntPtr.Zero)
                        root = AutomationElement.FromHandle(targetHwnd);
                }

                if (root != null)
                {
                    try { rootName = CleanText(root.Current.Name, 120); } catch { }

                    var walker = TreeWalker.ControlViewWalker;
                    var queue = new Queue<Tuple<AutomationElement, int>>();
                    queue.Enqueue(Tuple.Create(root, 0));

                    int visited = 0;
                    int maxVisited = 2500;

                    while (queue.Count > 0 && results.Count < maxElements && visited < maxVisited && sw.ElapsedMilliseconds < timeoutMs)
                    {
                        var item = queue.Dequeue();
                        var el = item.Item1;
                        int depth = item.Item2;
                        visited++;

                        try
                        {
                            var cur = el.Current;
                            if (cur.IsOffscreen) continue;

                            Rect r = cur.BoundingRectangle;
                            if (r.IsEmpty || double.IsInfinity(r.Left) || double.IsNaN(r.Left) || r.Width < 4 || r.Height < 4)
                            {
                                // Still traverse children of root/windows with odd rects
                                if (depth > 1) continue;
                            }
                            else
                            {
                                // Check intersection with primary screen
                                if (r.Right <= 0 || r.Bottom <= 0 || r.Left >= screenW || r.Top >= screenH)
                                    continue;
                            }

                            string role = cur.ControlType.ProgrammaticName;
                            if (role.StartsWith("ControlType.")) role = role.Substring(12);

                            bool isInteractive = InteractiveRoles.Contains(role) || (includeText && role == "Text");
                            bool isRoot = (depth == 0);

                            if (!isRoot && isInteractive && !r.IsEmpty && r.Width >= 6 && r.Height >= 6 && r.Width < screenW * 0.98 && r.Height < screenH * 0.98)
                            {
                                int left = Math.Max(0, (int)Math.Round(r.Left));
                                int top = Math.Max(0, (int)Math.Round(r.Top));
                                int right = Math.Min(screenW, (int)Math.Round(r.Right));
                                int bottom = Math.Min(screenH, (int)Math.Round(r.Bottom));

                                if (right - left >= 6 && bottom - top >= 6)
                                {
                                    int cx = (left + right) / 2;
                                    int cy = (top + bottom) / 2;
                                    string name = CleanText(cur.Name);
                                    string autoId = CleanText(cur.AutomationId, 40);
                                    string val = "";

                                    if (role == "Edit" || role == "ComboBox" || role == "Document")
                                    {
                                        object pat;
                                        if (el.TryGetCurrentPattern(ValuePattern.Pattern, out pat))
                                        {
                                            try { val = CleanText(((ValuePattern)pat).Current.Value, 60); } catch { }
                                        }
                                    }

                                    // Deduplicate overlapping identical boxes (within 4px grid)
                                    string rectKey = (cx / 6) + "_" + (cy / 6) + "_" + ((right - left) / 12) + "_" + ((bottom - top) / 12);
                                    if (!seenRects.Contains(rectKey) && (name.Length > 0 || val.Length > 0 || autoId.Length > 0 || role == "Edit" || role == "Button"))
                                    {
                                        seenRects.Add(rectKey);
                                        results.Add(new ElementInfo
                                        {
                                            Id = results.Count + 1,
                                            Role = role,
                                            Name = name,
                                            Value = val,
                                            AutomationId = autoId,
                                            Left = left,
                                            Top = top,
                                            Right = right,
                                            Bottom = bottom,
                                            Cx = cx,
                                            Cy = cy,
                                            Nx = (int)Math.Round(left * 1000.0 / screenW),
                                            Ny = (int)Math.Round(top * 1000.0 / screenH),
                                            Nw = (int)Math.Round((right - left) * 1000.0 / screenW),
                                            Nh = (int)Math.Round((bottom - top) * 1000.0 / screenH),
                                            Ncx = (int)Math.Round(cx * 1000.0 / screenW),
                                            Ncy = (int)Math.Round(cy * 1000.0 / screenH),
                                            Enabled = cur.IsEnabled
                                        });
                                    }
                                }
                            }

                            if (depth < maxDepth && (isRoot || ContainerRoles.Contains(role) || role == "ListItem" || role == "TreeItem" || role == "TabItem" || role == "DataItem"))
                            {
                                AutomationElement child = null;
                                try { child = walker.GetFirstChild(el); } catch { }
                                int childCount = 0;
                                while (child != null && childCount < 120 && sw.ElapsedMilliseconds < timeoutMs)
                                {
                                    queue.Enqueue(Tuple.Create(child, depth + 1));
                                    childCount++;
                                    try { child = walker.GetNextSibling(child); } catch { break; }
                                }
                            }
                        }
                        catch
                        {
                            // Element vanished during traversal
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                Console.WriteLine("{\"error\":\"" + EscapeJson(ex.Message) + "\"}");
                return;
            }

            sw.Stop();

            var sb = new StringBuilder();
            sb.Append("{");
            sb.AppendFormat("\"window\":\"{0}\",", EscapeJson(rootName));
            sb.AppendFormat("\"hwnd\":{0},", targetHwnd.ToInt64());
            sb.AppendFormat("\"screen\":[{0},{1}],", screenW, screenH);
            sb.AppendFormat("\"elapsed_ms\":{0},", sw.ElapsedMilliseconds);
            sb.AppendFormat("\"count\":{0},", results.Count);
            sb.Append("\"elements\":[");
            for (int i = 0; i < results.Count; i++)
            {
                var e = results[i];
                if (i > 0) sb.Append(",");
                sb.Append("{");
                sb.AppendFormat("\"id\":{0},", e.Id);
                sb.AppendFormat("\"role\":\"{0}\",", EscapeJson(e.Role));
                sb.AppendFormat("\"name\":\"{0}\",", EscapeJson(e.Name));
                if (!string.IsNullOrEmpty(e.Value))
                    sb.AppendFormat("\"value\":\"{0}\",", EscapeJson(e.Value));
                if (!string.IsNullOrEmpty(e.AutomationId))
                    sb.AppendFormat("\"aid\":\"{0}\",", EscapeJson(e.AutomationId));
                sb.AppendFormat("\"px\":[{0},{1},{2},{3}],", e.Left, e.Top, e.Right, e.Bottom);
                sb.AppendFormat("\"center_px\":[{0},{1}],", e.Cx, e.Cy);
                sb.AppendFormat("\"norm\":[{0},{1},{2},{3}],", e.Nx, e.Ny, e.Nw, e.Nh);
                sb.AppendFormat("\"center\":[{0},{1}],", e.Ncx, e.Ncy);
                sb.AppendFormat("\"enabled\":{0}", e.Enabled ? "true" : "false");
                sb.Append("}");
            }
            sb.Append("]}");
            Console.WriteLine(sb.ToString());
        }
    }
}
