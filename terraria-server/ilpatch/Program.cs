// TerrariaServer.exe IL 插桩：在昼夜切换、血月、玩家死亡方法注入 Console.WriteLine，
// 使原版服务端（默认不打印这些事件）把事件写入控制台日志，供看板/AI 使用。
// 用法: dotnet run -- <TerrariaServer.exe> <out.exe>
using dnlib.DotNet;
using dnlib.DotNet.Emit;
using System.Linq;

if (args.Length < 2) { Console.WriteLine("usage: ilpatch <in.exe> <out.exe>"); return; }

var mod = ModuleDefMD.Load(args[0]);
TypeDef? GetTd(string full) => mod.Types.FirstOrDefault(t => t.FullName == full);

// 关键：服务器是 Mono（mscorlib），不能用运行时 typeof(Console)（会引用 .NET 8 的
// System.Console 8.0.0.0 程序集，Mono 下不存在 → FileNotFoundException 崩溃）。
// 必须手动构造对 mscorlib 的引用。
var corlib = mod.CorLibTypes.AssemblyRef;   // mscorlib
var consoleType = new TypeRefUser(mod, "System", "Console", corlib);
var writeLine = new MemberRefUser(mod, "WriteLine",
    MethodSig.CreateStatic(mod.CorLibTypes.Void, mod.CorLibTypes.String), consoleType);
var strType = new TypeRefUser(mod, "System", "String", corlib);
var strConcat = new MemberRefUser(mod, "Concat",
    MethodSig.CreateStatic(mod.CorLibTypes.String, mod.CorLibTypes.String, mod.CorLibTypes.String), strType);
// patched5：字符串前缀判断 String.StartsWith(string)（instance）
var startsWith = new MemberRefUser(mod, "StartsWith",
    MethodSig.CreateInstance(mod.CorLibTypes.Boolean, mod.CorLibTypes.String), strType);
// patched5：XNA Color(int r,int g,int b) 构造 —— 类型引用取自 ChatColors.ServerMessage 字段类型
TypeRefUser colorType = null;
MemberRefUser colorCtor = null;
var chatColorsTd = mod.Types.FirstOrDefault(t => t.FullName == "Terraria.Chat.ChatColors");
var serverMsgField = chatColorsTd?.FindField("ServerMessage");
if (serverMsgField != null && serverMsgField.FieldType is ClassOrValueTypeSig covts && covts.TypeDefOrRef is TypeRef tr) {
    colorType = new TypeRefUser(mod, tr.Namespace, tr.Name, tr.ResolutionScope);
    colorCtor = new MemberRefUser(mod, ".ctor",
        MethodSig.CreateInstance(mod.CorLibTypes.Void, mod.CorLibTypes.Int32, mod.CorLibTypes.Int32, mod.CorLibTypes.Int32),
        colorType);
}

int injected = 0;
void InjectStart(MethodDef md, Instruction[] instrs)
{
    var body = md.Body;
    if (body == null) { Console.WriteLine($"skip {md.Name} (no body)"); return; }
    var il = body.Instructions;
    for (int i = instrs.Length - 1; i >= 0; i--) il.Insert(0, instrs[i]);
    injected++;
    Console.WriteLine($"OK Terraria.Main::{md.Name} ({il.Count} instrs)");
}

var mainTd = GetTd("Terraria.Main");
if (mainTd == null) { Console.WriteLine("FATAL: Terraria.Main not found"); return; }
var playerTd = GetTd("Terraria.Player");
if (playerTd == null) { Console.WriteLine("FATAL: Terraria.Player not found"); return; }

// 昼夜切换（static）
var startDay = mainTd.FindMethod("UpdateTime_StartDay");
if (startDay == null) { Console.WriteLine("FATAL: UpdateTime_StartDay not found"); return; }
InjectStart(startDay, new[] {
    Instruction.Create(OpCodes.Ldstr, "[DAY] 进入白天"),
    Instruction.Create(OpCodes.Call, writeLine),
});

var startNight = mainTd.FindMethod("UpdateTime_StartNight");
if (startNight == null) { Console.WriteLine("FATAL: UpdateTime_StartNight not found"); return; }
InjectStart(startNight, new[] {
    Instruction.Create(OpCodes.Ldstr, "[NIGHT] 进入夜晚"),
    Instruction.Create(OpCodes.Call, writeLine),
});

// 血月：在 UpdateTime_StartNight 里 bloodMoon=true（stsfld，static）赋值后注入输出
{
    var snb = startNight.Body;
    bool bm = false;
    for (int i = 0; i < snb.Instructions.Count - 1; i++) {
        var ins = snb.Instructions[i];
        if (ins.OpCode == OpCodes.Stsfld && ins.Operand is FieldDef fd && fd.Name == "bloodMoon") {
            snb.Instructions.Insert(i + 1, Instruction.Create(OpCodes.Ldstr, "[BLOODMOON] 血月降临"));
            snb.Instructions.Insert(i + 2, Instruction.Create(OpCodes.Call, writeLine));
            bm = true;
            break;
        }
    }
    Console.WriteLine(bm ? "OK Terraria.Main::UpdateTime_StartNight (bloodMoon)" : "WARN: bloodMoon stsfld not found");
}

// 玩家死亡（instance）：Console.WriteLine("KILLME: " + this.name)
var nameField = playerTd.FindField("name");
if (nameField == null) { Console.WriteLine("FATAL: Player.name not found"); return; }
MethodDef? killMe = null;
foreach (var m in playerTd.Methods)
    if (m.Name == "KillMe" && m.Parameters.Count >= 3) { killMe = m; break; }
if (killMe == null) { Console.WriteLine("FATAL: Player.KillMe not found"); return; }
{
    var kb = killMe.Body;
    var il = kb.Instructions;
    Instruction[] instrs = new[] {
        Instruction.Create(OpCodes.Ldstr, "KILLME: "),
        Instruction.Create(OpCodes.Ldarg_0),
        Instruction.Create(OpCodes.Ldfld, nameField),
        Instruction.Create(OpCodes.Call, strConcat),
        Instruction.Create(OpCodes.Call, writeLine),
    };
    for (int i = instrs.Length - 1; i >= 0; i--) il.Insert(0, instrs[i]);
    injected++;
    Console.WriteLine($"OK Terraria.Player::KillMe ({il.Count} instrs)");
}

// Say 命令纯文本化（patched4）：原 Say 分支广播 NetworkText.FromKey("CLI.ServerMessage", text)，
// 客户端本地化渲染成 "<服务器> text"。改写为广播 FromLiteral(text) 原样文本，
// 使 AI 机器人可自行拼 "<bot名> 内容" 以玩家样式发言（不带 <服务器>/Server 前缀）。
//   原: Console.WriteLine(Language.GetTextValue("CLI.ServerMessage", text5));
//       ChatHelper.BroadcastChatMessage(NetworkText.FromKey("CLI.ServerMessage", text5), ChatColors.ServerMessage);
//   改: Console.WriteLine(text5);
//       ChatHelper.BroadcastChatMessage(NetworkText.FromLiteral(text5), ChatColors.ServerMessage);
static bool IsLdloc(Instruction ins) =>
    ins.OpCode == OpCodes.Ldloc || ins.OpCode == OpCodes.Ldloc_S ||
    ins.OpCode == OpCodes.Ldloc_0 || ins.OpCode == OpCodes.Ldloc_1 ||
    ins.OpCode == OpCodes.Ldloc_2 || ins.OpCode == OpCodes.Ldloc_3;

var sayM = mainTd.FindMethod("startDedInputCallBack");
if (sayM == null) { Console.WriteLine("FATAL: startDedInputCallBack not found"); return; }
var netTextTd = mod.Types.FirstOrDefault(t => t.FullName == "Terraria.Localization.NetworkText");
if (netTextTd == null) { Console.WriteLine("FATAL: NetworkText not found"); return; }
var fromLiteralM = netTextTd.FindMethod("FromLiteral");
if (fromLiteralM == null) { Console.WriteLine("FATAL: NetworkText.FromLiteral not found"); return; }
{
    var il = sayM.Body.Instructions;
    int sayFix = 0;
    for (int i = 0; i < il.Count; i++) {
        if (il[i].OpCode != OpCodes.Ldstr || il[i].Operand is not string s || s != "CLI.ServerMessage") continue;
        // 期望: ldstr key; ldloc text5; call GetTextValue/FromKey
        int j = i + 1;
        while (j < il.Count && IsLdloc(il[j])) { j++; break; }
        if (j - 1 >= il.Count) continue;
        int k = j;
        while (k < il.Count && il[k].OpCode != OpCodes.Call && il[k].OpCode != OpCodes.Callvirt) k++;
        if (k >= il.Count) continue;
        if (il[k].Operand is not IMethod callee) continue;
        if (callee.Name == "GetTextValue" || callee.Name == "FromKey") {
            bool isFromKey = callee.Name == "FromKey";
            // 从 ldstr(key) 到 call 之间的指令整体改写：
            // - GetTextValue(string,object)：ldstr→nop、call→nop（保留 ldloc text5 喂 Console.WriteLine）
            // - FromKey(string, params object[])：ldstr/ldc.i4/newarr/dup/stelem.ref 全部 nop（保留 ldloc），
            //   call 换成 NetworkText.FromLiteral(string)——栈上只剩 text5，避免 params 数组残留导致 JIT 崩溃
            for (int d = i; d <= k; d++) {
                if (d == k) {
                    if (isFromKey) { il[d].OpCode = OpCodes.Call; il[d].Operand = fromLiteralM; }
                    else il[d].OpCode = OpCodes.Nop;
                } else if (IsLdloc(il[d])) {
                    /* 保留 text5 的加载 */
                } else {
                    il[d].OpCode = OpCodes.Nop;
                }
            }
            sayFix++;
            Console.WriteLine($"OK Say plain-text fix #{sayFix} ({callee.Name})");
            i = k;
        }
    }
    Console.WriteLine(sayFix == 2 ? $"OK Terraria.Main::startDedInputCallBack (say x{sayFix})" : $"WARN: Say fix count={sayFix}/2");
    // startDedInputCallBack 含 try/catch 复杂控制流，dnlib 重算 max stack 会误报；
    // 我们注入的代码栈深不超过原方法其它路径，直接保留原 maxstack 值。
    sayM.Body.KeepOldMaxStack = true;
}

// patched8：say 广播颜色按 bot 名前缀着色（双色）。
// <扣哒> 粉 #f472b6、<小T> 绿 #4ade80、其余 ServerMessage。
// v1 被 mono 拒：推测 MaxStack 声明临界，v3 显式加大 MaxStack +8 重试。
{
    int colorFix = 0;
    if (colorCtor != null && startsWith != null) {
        var il = sayM.Body.Instructions;
        for (int idx = 0; idx < il.Count; idx++) {
            var ins = il[idx];
            if ((ins.OpCode != OpCodes.Call && ins.OpCode != OpCodes.Callvirt) ||
                ins.Operand is not IMethod im || (im.Name != "FromKey" && im.Name != "FromLiteral"))
                continue;
            int li = idx - 1;
            while (li >= 0 && !IsLdloc(il[li])) li--;
            if (li < 0) continue;
            Instruction txt = il[li];
            for (int p = idx + 1; p < Math.Min(il.Count, idx + 10); p++) {
                if (il[p].OpCode != OpCodes.Ldsfld || il[p].Operand is not FieldDef f2 || f2.Name != "ServerMessage")
                    continue;
                Instruction ld = (txt.OpCode == OpCodes.Ldloc || txt.OpCode == OpCodes.Ldloc_S)
                    ? Instruction.Create(txt.OpCode, (Local)txt.Operand) : Instruction.Create(txt.OpCode);
                Instruction ld2 = (txt.OpCode == OpCodes.Ldloc || txt.OpCode == OpCodes.Ldloc_S)
                    ? Instruction.Create(txt.OpCode, (Local)txt.Operand) : Instruction.Create(txt.OpCode);
                var l1 = Instruction.Create(OpCodes.Ldstr, "<扣哒>");
                var c1 = Instruction.Create(OpCodes.Callvirt, startsWith);
                var b1 = Instruction.Create(OpCodes.Brtrue_S, (Instruction)null);
                var l2 = Instruction.Create(OpCodes.Ldstr, "<小T>");
                var c2 = Instruction.Create(OpCodes.Callvirt, startsWith);
                var b2 = Instruction.Create(OpCodes.Brtrue_S, (Instruction)null);
                var fb = Instruction.Create(OpCodes.Ldsfld, f2);
                var bend = Instruction.Create(OpCodes.Br_S, (Instruction)null);
                var all = new List<Instruction> { ld, l1, c1, b1, ld2, l2, c2, b2, fb, bend };
                int pinkStart = all.Count;
                all.Add(Instruction.CreateLdcI4(244)); // 扣哒粉 #f472b6
                all.Add(Instruction.CreateLdcI4(114));
                all.Add(Instruction.CreateLdcI4(182));
                all.Add(Instruction.Create(OpCodes.Newobj, colorCtor));
                // 关键：pink 段末尾必须跳过 green 段，否则 fallthrough 再压一层绿导致栈不平衡
                var skipGreen = Instruction.Create(OpCodes.Br_S, (Instruction)null);
                all.Add(skipGreen);
                int greenStart = all.Count;
                all.Add(Instruction.CreateLdcI4(74));  // 小T绿 #4ade80
                all.Add(Instruction.CreateLdcI4(222));
                all.Add(Instruction.CreateLdcI4(128));
                all.Add(Instruction.Create(OpCodes.Newobj, colorCtor));
                Instruction afterOld = il[p + 1];   // 原 ldsfld 的后续指令 = END（green 自然落入）
                b1.Operand = all[pinkStart];
                b2.Operand = all[greenStart];
                bend.Operand = afterOld;
                skipGreen.Operand = afterOld;
                il[p].OpCode = all[0].OpCode;
                il[p].Operand = all[0].Operand;
                for (int q = 1; q < all.Count; q++) il.Insert(p + q, all[q]);
                colorFix++;
                break;
            }
        }
        // 分支选色峰值栈高于原 ldsfld，显式加大 MaxStack 防 mono 验证越界
        sayM.Body.MaxStack = (ushort)(sayM.Body.MaxStack + 8);
    }
    Console.WriteLine(colorFix == 1 ? $"OK say color fix (dual, {colorFix})" : $"WARN: say color count={colorFix}/1");
    sayM.Body.SimplifyBranches();
}

// patched6：入侵事件文本进服务端日志。
// InvasionWarning() 是入侵文本中枢（逼近/已经到达/被击退，按 invasionType 选本地化文本并广播给客户端），
// 在 BroadcastChatMessage 前补一行 Console.WriteLine("[EVENT] <中文文本>")，
// 看板与 AI 即可看到"哥布林军队已经到达！"这类消息。
{
    var invWarn = mainTd.FindMethod("InvasionWarning");
    int invFix = 0;
    if (invWarn != null) {
        var ilw = invWarn.Body.Instructions;
        int bcidx = -1;
        for (int i = 0; i < ilw.Count; i++) {
            if ((ilw[i].OpCode == OpCodes.Call || ilw[i].OpCode == OpCodes.Callvirt) &&
                ilw[i].Operand is IMethod mm && mm.Name == "BroadcastChatMessage") { bcidx = i; break; }
        }
        var lttd = mod.Types.FirstOrDefault(x => x.FullName == "Terraria.Localization.LocalizedText");
        var getValue = lttd?.FindMethod("get_Value");   // 模块内方法：MethodDef 可直接作 call 操作数
        if (bcidx > 0 && getValue != null) {
            int li = bcidx - 1;
            while (li >= 0 && !IsLdloc(ilw[li])) li--;
            if (li >= 0) {
                Instruction txt = ilw[li];
                Instruction ld2 = (txt.OpCode == OpCodes.Ldloc || txt.OpCode == OpCodes.Ldloc_S)
                    ? Instruction.Create(txt.OpCode, (Local)txt.Operand) : Instruction.Create(txt.OpCode);
                var seq = new List<Instruction> {
                    Instruction.Create(OpCodes.Ldstr, "[EVENT] "),
                    ld2,
                    Instruction.Create(OpCodes.Call, getValue),
                    Instruction.Create(OpCodes.Call, strConcat),
                    Instruction.Create(OpCodes.Call, writeLine),
                };
                for (int q = seq.Count - 1; q >= 0; q--) ilw.Insert(bcidx, seq[q]);
                invFix++;
            }
        }
    }
    Console.WriteLine(invFix == 1 ? $"OK Terraria.Main::InvasionWarning ([EVENT] console)" : $"WARN: invasion fix={invFix}/1");
}

// patched6c：城镇 NPC 到达（含旅商/无家 NPC 入住/常规入住）进服务端日志。
// 服务端在这三处广播 "Announcement.HasArrived" 横幅（客户端显示"XX 已到达！"），
// 在广播前补 Console.WriteLine("[NPC] <中文类型名> 已到达！")，看板与 AI 可见。
{
    var wgTd = GetTd("Terraria.WorldGen");
    var npcTd = GetTd("Terraria.NPC");
    var mainNpcField = mainTd.FindField("npc");
    var typeNameGet = npcTd?.FindMethod("get_TypeName");
    int npcFix = 0;
    if (wgTd != null && mainNpcField != null && typeNameGet != null) {
        foreach (var mname in new[] { "SpawnHomelessNPC", "SpawnTravelNPC", "SpawnTownNPC" }) {
            var md = wgTd.FindMethod(mname);
            if (md == null) { Console.WriteLine($"WARN: {mname} not found"); continue; }
            var iln = md.Body.Instructions;
            int bcastIdx = -1, annIdx = -1;
            for (int i = 0; i < iln.Count; i++) {
                if (iln[i].OpCode == OpCodes.Ldstr && iln[i].Operand is string s && s == "Announcement.HasArrived") annIdx = i;
                if (annIdx >= 0 && (iln[i].OpCode == OpCodes.Call || iln[i].OpCode == OpCodes.Callvirt) &&
                    iln[i].Operand is IMethod mm2 && mm2.Name == "BroadcastChatMessage") { bcastIdx = i; break; }
            }
            if (bcastIdx <= 0 || annIdx < 0) { Console.WriteLine($"WARN: {mname} no HasArrived broadcast"); continue; }
            // 广播参数里有 npc 实例：找 annIdx 之后到 Broadcast 前第一个 ldelem.ref，其前 ldloc 即 npc 下标
            int elemIdx = -1;
            for (int i = annIdx + 1; i < bcastIdx; i++) if (iln[i].OpCode == OpCodes.Ldelem_Ref) { elemIdx = i; break; }
            if (elemIdx < 0) { Console.WriteLine($"WARN: {mname} no ldelem"); continue; }
            int lidx = elemIdx - 1;
            while (lidx >= 0 && !IsLdloc(iln[lidx])) lidx--;
            if (lidx < 0) { Console.WriteLine($"WARN: {mname} no index ldloc"); continue; }
            Instruction ldN = (iln[lidx].OpCode == OpCodes.Ldloc || iln[lidx].OpCode == OpCodes.Ldloc_S)
                ? Instruction.Create(iln[lidx].OpCode, (Local)iln[lidx].Operand) : Instruction.Create(iln[lidx].OpCode);
            var seq = new List<Instruction> {
                Instruction.Create(OpCodes.Ldstr, "[NPC] "),
                Instruction.Create(OpCodes.Ldsfld, mainNpcField),
                ldN,
                Instruction.Create(OpCodes.Ldelem_Ref),
                Instruction.Create(OpCodes.Callvirt, typeNameGet),
                Instruction.Create(OpCodes.Call, strConcat),
                Instruction.Create(OpCodes.Ldstr, " 已到达！"),
                Instruction.Create(OpCodes.Call, strConcat),
                Instruction.Create(OpCodes.Call, writeLine),
            };
            for (int q = seq.Count - 1; q >= 0; q--) iln.Insert(bcastIdx, seq[q]);
            npcFix++;
            Console.WriteLine($"OK WorldGen::{mname} ([NPC] arrived console)");
        }
    }
    Console.WriteLine(npcFix == 3 ? $"OK NPC arrival fix ({npcFix}/3)" : $"WARN: NPC arrival count={npcFix}/3");
}

// patched9：世界事件补全 —— 日食 / Boss 苏醒 / Boss 被击败 进服务端日志（前缀 [EVENT]，面板与 AI 已识别）
{
    // 1) 日食：UpdateTime_StartDay 内 eclipse=true 赋值后（该赋值在方法内唯一）
    var std = mainTd.FindMethod("UpdateTime_StartDay");
    if (std != null) {
        var ils = std.Body.Instructions;
        bool ecl = false;
        for (int i = 0; i < ils.Count - 1; i++) {
            if (ils[i].OpCode == OpCodes.Stsfld && ils[i].Operand is FieldDef fe && fe.Name == "eclipse") {
                ils.Insert(i + 1, Instruction.Create(OpCodes.Ldstr, "[EVENT] 日食发生，今天白天也会刷怪！"));
                ils.Insert(i + 2, Instruction.Create(OpCodes.Call, writeLine));
                ecl = true;
                break;
            }
        }
        Console.WriteLine(ecl ? "OK Terraria.Main::UpdateTime_StartDay (eclipse)" : "WARN: eclipse stsfld not found");
    }
    var npcTd9 = GetTd("Terraria.NPC");
    var langTd = GetTd("Terraria.Lang");
    var npcNameVal = langTd?.FindMethod("GetNPCNameValue");
    // 2) Boss 苏醒：SpawnOnPlayer(npcType=arg1) 入口打中文名
    var spawnOn = npcTd9?.FindMethod("SpawnOnPlayer");
    if (spawnOn != null && npcNameVal != null) {
        InjectStart(spawnOn, new[] {
            Instruction.Create(OpCodes.Ldstr, "[EVENT] "),
            Instruction.Create(OpCodes.Ldarg_1),
            Instruction.Create(OpCodes.Call, npcNameVal),
            Instruction.Create(OpCodes.Call, strConcat),
            Instruction.Create(OpCodes.Ldstr, " 已苏醒！"),
            Instruction.Create(OpCodes.Call, strConcat),
            Instruction.Create(OpCodes.Call, writeLine),
        });
        Console.WriteLine("OK Terraria.NPC::SpawnOnPlayer (spawn)");
    } else {
        Console.WriteLine("WARN: SpawnOnPlayer / GetNPCNameValue not found");
    }
    // 3) Boss 被击败：DoDeathEvents_CelebrateBossDeath 广播前打 this.TypeName
    var npcLoot = npcTd9?.FindMethod("DoDeathEvents_CelebrateBossDeath");
    if (npcLoot != null) {
        var tnameGet = npcTd9.FindMethod("get_TypeName");
        var iln = npcLoot.Body.Instructions;
        int dcount = 0;
        for (int i = 0; i < iln.Count; i++) {
            if ((iln[i].OpCode == OpCodes.Ldstr && iln[i].Operand is string sk && sk.Contains("HasBeenDefeated")) ||
                (iln[i].OpCode == OpCodes.Call && iln[i].Operand is IMethod bm && bm.Name == "BroadcastChatMessage")) {
                // 定位到广播或其 key 处；统一插到该条之前（此方法即 Boss 击败庆祝，方法体开头插亦可）
                var seq = new List<Instruction> {
                    Instruction.Create(OpCodes.Ldstr, "[EVENT] "),
                    Instruction.Create(OpCodes.Ldarg_0),
                    Instruction.Create(OpCodes.Callvirt, tnameGet),
                    Instruction.Create(OpCodes.Call, strConcat),
                    Instruction.Create(OpCodes.Ldstr, " 已被击败！"),
                    Instruction.Create(OpCodes.Call, strConcat),
                    Instruction.Create(OpCodes.Call, writeLine),
                };
                for (int q = seq.Count - 1; q >= 0; q--) iln.Insert(i, seq[q]);
                dcount++;
                break;
            }
        }
        Console.WriteLine(dcount >= 1 ? $"OK Terraria.NPC::DoDeathEvents_CelebrateBossDeath (defeated x{dcount})" : "WARN: celebrate boss death not found");
        npcLoot.Body.SimplifyBranches();
    }
}

mod.Write(args[1]);
Console.WriteLine($"saved {args[1]} ({injected} injections)");
