"""Generates reep-api-load.jmx. Edit THIS and re-run it; the .jmx is its output.\n\n    python3 testing/jmeter/build_plan.py\n"""
from xml.sax.saxutils import escape as e

def prop(kind, name, val):
    return f'<{kind}Prop name="{name}">{e(str(val))}</{kind}Prop>'
def s(n,v): return prop("string",n,v)
def b(n,v): return prop("bool",n,"true" if v else "false")
def i(n,v): return prop("int",n,v)
def l(n,v): return prop("long",n,v)

def http(name, method, path, body=None, extra=""):
    args = ""
    if body is not None:
        args = f'''<boolProp name="HTTPSampler.postBodyRaw">true</boolProp>
<elementProp name="HTTPsampler.Arguments" elementType="Arguments"><collectionProp name="Arguments.arguments">
<elementProp name="" elementType="HTTPArgument">{b("HTTPArgument.always_encode",False)}{s("Argument.value",body)}{s("Argument.metadata","=")}</elementProp>
</collectionProp></elementProp>'''
    else:
        args = '<elementProp name="HTTPsampler.Arguments" elementType="Arguments"><collectionProp name="Arguments.arguments"/></elementProp>'
    return f'''<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="{e(name)}" enabled="true">
{args}{s("HTTPSampler.path",path)}{s("HTTPSampler.method",method)}{b("HTTPSampler.follow_redirects",False)}{b("HTTPSampler.use_keepalive",True)}
</HTTPSamplerProxy>
<hashTree>{extra}</hashTree>'''

def assert_code(codes="200"):
    return f'''<ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="Status {codes}" enabled="true">
<collectionProp name="Asserion.test_strings"><stringProp name="0">{codes}</stringProp></collectionProp>
{s("Assertion.custom_message","")}{s("Assertion.test_field","Assertion.response_code")}{b("Assertion.assume_success",False)}{i("Assertion.test_type",8)}
</ResponseAssertion><hashTree/>'''

def sla():
    return f'''<DurationAssertion guiclass="DurationAssertionGui" testclass="DurationAssertion" testname="SLA ${{__P(sla_ms,1000)}} ms" enabled="true">
{s("DurationAssertion.duration","${__P(sla_ms,1000)}")}</DurationAssertion><hashTree/>'''

def json_assert(path, expect):
    return f'''<JSONPathAssertion guiclass="JSONPathAssertionGui" testclass="JSONPathAssertion" testname="JSON {e(path)}" enabled="true">
{s("JSON_PATH",path)}{s("EXPECTED_VALUE",expect)}{b("JSONVALIDATION",True)}{b("EXPECT_NULL",False)}{b("INVERT",False)}{b("ISREGEX",False)}
</JSONPathAssertion><hashTree/>'''

def thread_group(name, threads, ramp, dur, body):
    return f'''<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="{e(name)}" enabled="true">
{s("ThreadGroup.on_sample_error","continue")}
<elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController">{b("LoopController.continue_forever",False)}{i("LoopController.loops",-1)}</elementProp>
{s("ThreadGroup.num_threads",threads)}{s("ThreadGroup.ramp_time",ramp)}{b("ThreadGroup.scheduler",True)}{s("ThreadGroup.duration",dur)}{s("ThreadGroup.delay","")}{b("ThreadGroup.same_user_on_next_iteration",True)}
</ThreadGroup>
<hashTree>{body}</hashTree>'''

def timer():
    return f'''<UniformRandomTimer guiclass="UniformRandomTimerGui" testclass="UniformRandomTimer" testname="Think time" enabled="true">
{s("ConstantTimer.delay","${__P(think_ms,500)}")}{s("RandomTimer.range","${__P(think_jitter_ms,500)}")}</UniformRandomTimer><hashTree/>'''

pick = f'''<JSR223PreProcessor guiclass="TestBeanGUI" testclass="JSR223PreProcessor" testname="One account per virtual user" enabled="true">
{s("scriptLanguage","groovy")}{s("parameters","")}{s("filename","")}{s("cacheKey","true")}
{s("script", """// Thread N (1-based) signs in as loadtestNNN. Deterministic on purpose: REEP keeps ONE
// live session per account, so two virtual users on one account retire each other (401).
// A CSV Data Set hands out a new line on EVERY iteration and caused exactly that.
int n = ctx.getThreadNum() + 1
int max = (props.get('max_users') ?: '100') as int
if (n > max) { throw new IllegalStateException('threads > load-test accounts (' + max + ')') }
vars.put('email', String.format('%s%03d@bgscet.ac.in', props.get('user_prefix') ?: 'loadtest', n))
vars.put('password', props.get('user_password') ?: 'LoadTest#2026')""")}
</JSR223PreProcessor><hashTree/>'''
login = http("POST /api/auth/login", "POST", "/api/auth/login",
             '{"email":"${email}","password":"${password}"}',
             pick + assert_code("200") + json_assert("$.role", "STUDENT"))
once = f'''<OnceOnlyController guiclass="OnceOnlyControllerGui" testclass="OnceOnlyController" testname="Sign in once per virtual user" enabled="true"/>
<hashTree>{login}</hashTree>'''

student_paths = ["/api/auth/me", "/api/student/dashboard", "/api/student/programme", "/api/student/jobs",
                 "/api/student/leaderboards", "/api/student/ledger", "/api/student/badges",
                 "/api/student/timesheet", "/api/student/placement-readiness", "/api/student/profile"]
journey = "".join(http(f"GET {p}", "GET", p, extra=assert_code("200") + sla()) for p in student_paths)

csv = f'''<CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" testname="Load-test accounts (users.csv)" enabled="true">
{s("filename","${__P(users_csv,data/users.csv)}")}{s("fileEncoding","UTF-8")}{s("variableNames","email,password")}{b("ignoreFirstLine",True)}{s("delimiter",",")}{b("quotedData",False)}{b("recycle",True)}{b("stopThread",False)}{s("shareMode","shareMode.all")}
</CSVDataSet><hashTree/>'''

student_tg = thread_group("Student journey (authenticated)", "${__P(threads,50)}", "${__P(rampup,30)}", "${__P(duration,180)}",
    '<CookieManager guiclass="CookiePanel" testclass="CookieManager" testname="Session cookie (reep_session)" enabled="true"><collectionProp name="CookieManager.cookies"/>'
    + b("CookieManager.clearEachIteration", False) + b("CookieManager.controlledByThreadGroup", False) + '</CookieManager><hashTree/>'
    + once + journey + timer())

public_paths = ["/health", "/api/auth/sso/status", "/api/register/hierarchy"]
public_tg = thread_group("Public endpoints (anonymous)", "${__P(public_threads,5)}", "${__P(rampup,30)}", "${__P(duration,180)}",
    "".join(http(f"GET {p}", "GET", p, extra=assert_code("200") + sla()) for p in public_paths) + timer())

defaults = f'''<ConfigTestElement guiclass="HttpDefaultsGui" testclass="ConfigTestElement" testname="HTTP defaults" enabled="true">
<elementProp name="HTTPsampler.Arguments" elementType="Arguments"><collectionProp name="Arguments.arguments"/></elementProp>
{s("HTTPSampler.domain","${__P(host,localhost)}")}{s("HTTPSampler.port","${__P(port,3300)}")}{s("HTTPSampler.protocol","${__P(protocol,http)}")}{s("HTTPSampler.connect_timeout","5000")}{s("HTTPSampler.response_timeout","30000")}{s("HTTPSampler.implementation","HttpClient4")}
</ConfigTestElement><hashTree/>
<HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="JSON headers" enabled="true"><collectionProp name="HeaderManager.headers">
<elementProp name="" elementType="Header">{s("Header.name","Content-Type")}{s("Header.value","application/json")}</elementProp>
<elementProp name="" elementType="Header">{s("Header.name","Accept")}{s("Header.value","application/json")}</elementProp>
<elementProp name="" elementType="Header">{s("Header.name","User-Agent")}{s("Header.value","REEP-JMeter-LoadTest/1.0")}</elementProp>
</collectionProp></HeaderManager><hashTree/>'''

plan = f'''<?xml version="1.0" encoding="UTF-8"?>
<!-- REEP API performance test plan. Run NON-GUI only (see testing/jmeter/run.sh).
     Every knob is a JMeter property: -Jthreads -Jrampup -Jduration -Jthink_ms -Jsla_ms -Jhost -Jport -Jprotocol -Jmax_users -Juser_prefix -Juser_password -->
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
<hashTree>
<TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="REEP API - load, stress, spike, soak" enabled="true">
{b("TestPlan.functional_mode",False)}{b("TestPlan.serialize_threadgroups",False)}
<elementProp name="TestPlan.user_defined_variables" elementType="Arguments"><collectionProp name="Arguments.arguments"/></elementProp>
</TestPlan>
<hashTree>{defaults}{student_tg}{public_tg}</hashTree>
</hashTree>
</jmeterTestPlan>
'''
import pathlib
pathlib.Path(__file__).with_name("reep-api-load.jmx").write_text(plan)
