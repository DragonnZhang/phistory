# openagents — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## coax/lib/microcoder/action.coax

Source: https://github.com/OpenAgentsInc/openagents/blob/4af75e9b2548a5b0cff5a249e3dd4d3032cd1e4c/coax/lib/microcoder/action.coax

SHA-256: `9ecebad36b881b11bda2ef6e0ab3f6afbe2eb5f895fb3f25da4e6cffd015300e`

```
// The action (docs/coder/microcoder.md, "The action"): each step is one
// model call with a strict JSON schema, and the reply is this object:
//
//   { "rationale": "why this step, for the log; never shown as the reply",
//     "commands":  ["shell commands to run, in order"],
//     "view":      ["files to show in full in the next step"],
//     "finished":  false,
//     "reply":     "on the finishing step, the answer addressed to the user",
//     "ask":       "none | question" }
//
// The old NextAction without freeze_tests and expand. mc_action_parse holds
// a reply to the schema and to its meaning: finished and ask: question each
// need a reply.

#load "state.coax";

MC_SCHEMA_NAME :: "next_action";
MC_SCHEMA :: "{\"type\":\"object\",\"properties\":{\"rationale\":{\"type\":\"string\"},\"commands\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}},\"view\":{\"type\":\"array\",\"items\":{\"type\":\"string\"}},\"finished\":{\"type\":\"boolean\"},\"reply\":{\"type\":\"string\"},\"ask\":{\"type\":\"string\",\"enum\":[\"none\",\"question\"]}},\"required\":[\"rationale\",\"commands\",\"view\",\"finished\",\"reply\",\"ask\"],\"additionalProperties\":false}";

MC_MAX_COMMANDS :: 32;
MC_MAX_VIEWS :: 16;

McAction :: struct {
  rationale: string;
  commands: [MC_MAX_COMMANDS] string; nc: int;
  view: [MC_MAX_VIEWS] string; nv: int;
  finished: bool;
  reply: string;
  asks: bool;                    // ask: question
}

// The reply as an action, its strings decoded into ar (they live as long as
// text and ar). What's wrong with it, or "".
mc_action_parse :: (ar: [] ClArena, text: string, a: [] McAction) -> string {
  a[0] = McAction.{};
  if !json_valid(text) { return "the reply is not JSON"; }
  if !cl_valid(text, "s:rationale a:commands s:commands.* a:view s:view.* b:finished s:reply s:ask=none|question") {
    return "the reply does not match the next_action schema";
  }
  a[0].rationale = cl_str(ar, text, "rationale");
  cmds := cl_raw(text, "commands");
  nc := cl_count(cmds);
  if nc > MC_MAX_COMMANDS { return "the reply has more than 32 commands"; }
  for i: 0..<nc { a[0].commands[i] = cl_text(ar, cmds, cl_item(cmds, i)); }
  a[0].nc = nc;
  views := cl_raw(text, "view");
  nv := cl_count(views);
  if nv > MC_MAX_VIEWS { return "the reply asks to view more than 16 files"; }
  for i: 0..<nv { a[0].view[i] = cl_text(ar, views, cl_item(views, i)); }
  a[0].nv = nv;
  a[0].finished = cl_bool(text, "finished").value;
  a[0].reply = cl_str(ar, text, "reply");
  a[0].asks = cl_str(ar, text, "ask") == "question";
  if ar[0].full { return "the reply does not fit its buffer"; }
  if a[0].finished && a[0].reply.count == 0 { return "finished is true but reply is empty"; }
  if a[0].asks && a[0].reply.count == 0 { return "ask is question but reply is empty"; }
  for i: 0..<nc { if a[0].commands[i].count == 0 { return "a command is empty"; } }
  return "";
}

// ---------------------------------------------------------------- checks

#test "the schema is JSON, and a plain answer parses as finished with no commands" {
  #assert json_valid(MC_SCHEMA);
  ab: [1024] u8; ar: [1] ClArena; ar[0] = ClArena.{buf = ab[..]};
  a: [1] McAction;
  why := mc_action_parse(ar[..], "{\"rationale\":\"arithmetic\",\"commands\":[],\"view\":[],\"finished\":true,\"reply\":\"4\",\"ask\":\"none\"}", a[..]);
  #assert why == "" && a[0].finished && a[0].reply == "4" && a[0].nc == 0 && a[0].nv == 0 && !a[0].asks;
  why = mc_action_parse(ar[..], "{\"rationale\":\"look\",\"commands\":[\"ls *.swift | wc -l\",\"echo \\\"hi\\\"\"],\"view\":[\"README.md\"],\"finished\":false,\"reply\":\"\",\"ask\":\"none\"}", a[..]);
  #assert why == "" && a[0].nc == 2 && a[0].commands[1] == "echo \"hi\"" && a[0].view[0] == "README.md" && !a[0].finished;
}

#test "replies that fail the schema or its meaning are refused with a reason" {
  ab: [1024] u8; ar: [1] ClArena; ar[0] = ClArena.{buf = ab[..]};
  a: [1] McAction;
  #assert mc_action_parse(ar[..], "4", a[..]) == "the reply does not match the next_action schema";
  #assert mc_action_parse(ar[..], "The answer is 4.", a[..]) == "the reply is not JSON";
  #assert mc_action_parse(ar[..], "{\"rationale\":\"x\",\"commands\":[],\"view\":[],\"finished\":true,\"reply\":\"4\"}", a[..]) != "";
  #assert mc_action_parse(ar[..], "{\"rationale\":\"x\",\"commands\":[1],\"view\":[],\"finished\":true,\"reply\":\"4\",\"ask\":\"none\"}", a[..]) != "";
  #assert mc_action_parse(ar[..], "{\"rationale\":\"x\",\"commands\":[],\"view\":[],\"finished\":true,\"reply\":\"4\",\"ask\":\"maybe\"}", a[..]) != "";
  #assert mc_action_parse(ar[..], "{\"rationale\":\"x\",\"commands\":[],\"view\":[],\"finished\":true,\"reply\":\"\",\"ask\":\"none\"}", a[..]) == "finished is true but reply is empty";
  #assert mc_action_parse(ar[..], "{\"rationale\":\"x\",\"commands\":[],\"view\":[],\"finished\":false,\"reply\":\"\",\"ask\":\"question\"}", a[..]) == "ask is question but reply is empty";
}

```

## coax/lib/microcoder/tools.coax

Source: https://github.com/OpenAgentsInc/openagents/blob/4af75e9b2548a5b0cff5a249e3dd4d3032cd1e4c/coax/lib/microcoder/tools.coax

SHA-256: `e83bbf576bfd35630d4941c854a3bacd01c687224aee40ff82ce66297ee5ad31`

```
// Microcoder's tools in v1 (docs/coder/microcoder.md, "Tools in v1"):
//   commands  run by /bin/sh in the working directory through
//             coax/platform/process-native.c (link it), 30 s each; past it the
//             whole process group is killed and the command recorded as timed
//             out. stdout and stderr are kept as a head and a tail.
//   view      files under the working directory only, 64 KB each.
// Nothing else: no network tool, no editing outside the working directory;
// writes happen through commands, so they show in the transcript. With
// route.local_work under 0.5 the loop refuses commands before they reach
// here (loop.coax). mc_run_tools runs a pending action's commands and views
// into the loop.

#load "loop.coax";

MC_COMMAND_SECONDS :: 30;
MC_VIEW_BYTES :: 65536;
MC_KEEP_HEAD :: 5900;            // with the tail and the gap line, under the state's 6,000 + 6,000 cut
MC_KEEP_TAIL :: 5900;

// What one command printed: its head, and a ring of its latest bytes.
McOutput :: struct {
  head: [MC_KEEP_HEAD] u8; nh: int;
  tail: [MC_KEEP_TAIL] u8; nt: int; at: int;    // ring: nt bytes held, at the next write
  dropped: int;                  // bytes between head and tail left out
  text: [12100] u8; ntext: int;  // assembled by mc_output_text
}

mc_output_byte :: (o: [] McOutput, c: u8) {
  if o[0].nh < MC_KEEP_HEAD { o[0].head[o[0].nh] = c; o[0].nh += 1; return; }
  if o[0].nt == MC_KEEP_TAIL { o[0].dropped += 1; } else { o[0].nt += 1; }
  o[0].tail[o[0].at] = c;
  o[0].at = (o[0].at + 1) % MC_KEEP_TAIL;
}

mc_output_text :: (o: [] McOutput) -> string {
  n := 0;
  for i: 0..<o[0].nh { o[0].text[n] = o[0].head[i]; n += 1; }
  if o[0].dropped > 0 {
    num: [24] u8;
    k := json_write_int(num[..], 0, o[0].dropped);
    for c: "\n[... " { o[0].text[n] = c; n += 1; }
    for i: 0..<k { o[0].text[n] = num[i]; n += 1; }
    for c: " bytes left out ...]\n" { o[0].text[n] = c; n += 1; }
  }
  start := ifx o[0].nt == MC_KEEP_TAIL then o[0].at else 0;
  for i: 0..<o[0].nt { o[0].text[n] = o[0].tail[(start + i) % MC_KEEP_TAIL]; n += 1; }
  o[0].ntext = n;
  return cast(string) o[0].text[0..<n];
}

McCommandResult :: struct {
  exit: int;                     // -1 when killed (timed_out) or it could not start
  timed_out: bool;
  output: string;                // in the McOutput given
}

mc_proc_arg :: (h: int, field: int, s: string) -> bool {
  for c: s { if c == 0 || cxproc_push(h, field, cast(int) c) != 0 { return false; } }
  return cxproc_end(h, field) == 0;
}

// Runs command by /bin/sh -c in cwd, stdin closed, for at most seconds.
mc_run_command :: (cwd: string, command: string, seconds: int, o: [] McOutput) -> McCommandResult {
  o[0].nh = 0; o[0].nt = 0; o[0].at = 0; o[0].dropped = 0;
  r := McCommandResult.{exit = -1};
  h := cxproc_new();
  if h < 0 { for c: "no process slot is free" { mc_output_byte(o, c); } r.output = mc_output_text(o); return r; }
  ok := mc_proc_arg(h, CL_FIELD_ARG, "/bin/sh") && mc_proc_arg(h, CL_FIELD_ARG, "-c") && mc_proc_arg(h, CL_FIELD_ARG, command);
  if cwd.count > 0 { ok = ok && mc_proc_arg(h, CL_FIELD_CWD, cwd); }
  if !ok || cxproc_spawn(h) != 0 {
    x := cxproc_release(h);
    for c: "the command could not start" { mc_output_byte(o, c); }
    r.output = mc_output_text(o); return r;
  }
  x := cxproc_close_stdin(h);
  deadline := cxproc_now_ms() + seconds * 1000;
  lines := 0;
  while true {
    left := deadline - cxproc_now_ms();
    if left <= 0 { r.timed_out = true; break; }
    n := cxproc_read_line(h, left);
    if n == CL_TIMEOUT { r.timed_out = true; break; }
    if n == CL_TOO_LONG { for c: "[a line too long to keep]" { mc_output_byte(o, c); } mc_output_byte(o, '\n'); continue; }
    if n < 0 { break; }
    for i: 0..<n { mc_output_byte(o, cast(u8) cxproc_line_byte(h, i)); }
    mc_output_byte(o, '\n');
    lines += 1;
  }
  if r.timed_out {
    k := cxproc_kill(h, 9);
    w := cxproc_wait(h, 2000);
  } else {
    r.exit = cxproc_wait(h, 5000);
    if r.exit < 0 { k := cxproc_kill(h, 9); r.exit = -1; r.timed_out = true; }
  }
  // stderr after stdout, as it would read in a terminal when they don't interleave
  ne := cxproc_stderr_count(h);
  for i: 0..<ne { mc_output_byte(o, cast(u8) cxproc_stderr_byte(h, i)); }
  y := cxproc_release(h);
  if r.timed_out {
    msg: [64] u8;
    m := json_raw(msg[..], 0, "\n[killed after ");
    m = json_write_int(msg[..], m, seconds);
    m = json_raw(msg[..], m, " s]");
    for i: 0..<m { mc_output_byte(o, msg[i]); }
  }
  r.output = mc_output_text(o);
  return r;
}

// Whether path stays under the working directory: relative with no ".."
// part, or absolute under cwd.
mc_path_inside :: (cwd: string, path: string) -> bool {
  if path.count == 0 { return false; }
  p := path;
  if p[0] == '/' {
    if cwd.count == 0 || p.count <= cwd.count + 1 || p[0..<cwd.count] != cwd || p[cwd.count] != '/' { return false; }
    p = cast(string) p[cwd.count + 1..<p.count];
  }
  start := 0;
  for i: 0..<p.count + 1 {
    if i == p.count || p[i] == '/' {
      if i - start == 2 && p[start] == '.' && p[start + 1] == '.' { return false; }
      start = i + 1;
    }
  }
  for c: p { if c == 0 { return false; } }
  return true;
}

McViewResult :: struct {
  found: bool;
  content: string;               // the file, or why it isn't shown
}

// Reads path under cwd (64 KB at most) into buf, through head(1).
mc_view_file :: (cwd: string, path: string, buf: [] u8) -> McViewResult {
  if !mc_path_inside(cwd, path) { return McViewResult.{content = "not shown: the path leaves the working directory"}; }
  h := cxproc_new();
  if h < 0 { return McViewResult.{content = "not shown: no process slot is free"}; }
  num: [24] u8;
  nn := json_write_int(num[..], 0, MC_VIEW_BYTES + 1);
  ok := mc_proc_arg(h, CL_FIELD_ARG, "/usr/bin/head") && mc_proc_arg(h, CL_FIELD_ARG, "-c") && mc_proc_arg(h, CL_FIELD_ARG, cast(string) num[0..<nn]);
  ok = ok && mc_proc_arg(h, CL_FIELD_ARG, "--") && mc_proc_arg(h, CL_FIELD_ARG, path);
  if cwd.count > 0 { ok = ok && mc_proc_arg(h, CL_FIELD_CWD, cwd); }
  if !ok || cxproc_spawn(h) != 0 { x := cxproc_release(h); return McViewResult.{content = "not shown: could not read it"}; }
  x := cxproc_close_stdin(h);
  n := 0; cut := false; binary := false; lines := 0;
  while true {
    k := cxproc_read_line(h, 10000);
    if k < 0 && k != CL_TOO_LONG { break; }
    if k == CL_TOO_LONG { cut = true; continue; }
    if lines > 0 { if n < buf.count && n < MC_VIEW_BYTES { buf[n] = '\n'; n += 1; } else { cut = true; } }
    for i: 0..<k {
      c := cast(u8) cxproc_line_byte(h, i);
      if c == 0 { binary = true; }
      if n < buf.count && n < MC_VIEW_BYTES { buf[n] = c; n += 1; } else { cut = true; }
    }
    lines += 1;
  }
  status := cxproc_wait(h, 5000);
  y := cxproc_release(h);
  if status != 0 { return McViewResult.{content = "not shown: no such file, or it can't be read"}; }
  if binary { return McViewResult.{found = true, content = "[a binary file: not shown]"}; }
  if cut && n > 0 { return McViewResult.{found = true, content = cast(string) buf[0..<n]}; }
  return McViewResult.{found = true, content = cast(string) buf[0..<n]};
}

McToolBuffers :: struct {
  out: [1] McOutput;
  view: [65536] u8;
}

// Runs the pending action's commands in order, then its views, into the
// loop; then mc_tools_done. Each command and file is recorded as it ends.
// A command that fails does not stop the ones after it.
mc_run_tools :: (l: [] McLoop, cwd: string, b: [] McToolBuffers) -> int {
  a := mc_pending(l);
  for i: 0..<a.nc {
    r := mc_run_command(cwd, a.commands[i], MC_COMMAND_SECONDS, b[0].out[..]);
    mc_command(l, a.commands[i], r.exit, r.timed_out, r.output);
  }
  for i: 0..<a.nv {
    v := mc_view_file(cwd, a.view[i], b[0].view[..]);
    mc_view(l, a.view[i], v.found, v.content);
  }
  return mc_tools_done(l);
}

// ---------------------------------------------------------------- checks

#test "output keeps a head and a tail and says how much it left out" {
  o: [1] McOutput;
  for i: 0..<20000 { mc_output_byte(o[..], cast(u8) ('a' + i % 26)); }
  t := mc_output_text(o[..]);
  #assert o[0].dropped == 20000 - MC_KEEP_HEAD - MC_KEEP_TAIL;
  #assert t.count < 12000 && t[0] == 'a' && t[t.count - 1] == cast(u8) ('a' + 19999 % 26);
  o[0] = McOutput.{};
  for c: "short\n" { mc_output_byte(o[..], c); }
  #assert mc_output_text(o[..]) == "short\n";
}

#test "paths stay under the working directory" {
  #assert mc_path_inside("/tmp/demo", "a.swift") && mc_path_inside("/tmp/demo", "src/x/y.txt") && mc_path_inside("/tmp/demo", "/tmp/demo/a.swift");
  #assert !mc_path_inside("/tmp/demo", "../etc/passwd") && !mc_path_inside("/tmp/demo", "src/../../x") && !mc_path_inside("/tmp/demo", "/etc/passwd");
  #assert !mc_path_inside("/tmp/demo", "/tmp/demox/a") && !mc_path_inside("/tmp/demo", "") && mc_path_inside("/tmp/demo", "..hidden");
}

```
