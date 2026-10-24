"""只读服务验收：签发演示令牌后查询，不提交业务申请、不清理任何数据库。"""
import argparse
import asyncio
import json
from pathlib import Path


async def verify(args):
    import httpx
    results=[]
    async with httpx.AsyncClient(timeout=10,trust_env=False) as client:
        async def check(name,url,statuses=(200,),token=None):
            try:
                r=await client.get(url,headers={"Authorization":f"Bearer {token}"} if token else {})
                result=dict(check=name,status=r.status_code,passed=r.status_code in statuses)
                if name=="ai_health" and r.status_code==200: result["runtime_mode"]=r.json().get("runtime_mode")
            except Exception as e: result=dict(check=name,passed=False,error=type(e).__name__)
            results.append(result)
            return r if 'r' in locals() else None
        business=await check("business_health",args.business+"/health")
        await check("ai_health",args.ai+"/health")
        await check("customer_openapi",args.customer+"/openapi.json")
        await check("user_frontend",args.user_frontend)
        await check("admin_frontend",args.admin_frontend)
        # Demo 令牌入口只用于明确返回 mode=demo 的业务服务。
        if business is not None and business.status_code==200 and business.json().get("mode")=="demo":
            tokens={}
            for uid in ("user_001","user_002"):
                r=await client.post(args.business+"/api/v1/auth/demo-token",json={"user_id":uid,"role":"customer"})
                if r.status_code!=200:
                    results.append(dict(check="demo_auth",passed=False,status=r.status_code))
                    continue
                tokens[uid]=r.json()["access_token"]
            if "user_001" in tokens:
                token=tokens["user_001"]
                orders=await check("own_orders",args.business+"/api/v1/orders",token=token)
                await check("customer_history",args.customer+"/api/v1/chat/history",token=token)
                await check("customer_cannot_read_admin",args.ai+"/api/v1/runs",(401,403),token=token)
                if orders is not None and orders.status_code==200:
                    data=orders.json().get("data") or []
                    if data:
                        oid=data[0]["id"]
                        for suffix in ("","/invoice","/service-progress"):
                            await check("order"+suffix,args.business+f"/api/v1/orders/{oid}{suffix}",token=token)
                        if "user_002" in tokens:
                            await check("other_user_order_denied",args.business+f"/api/v1/orders/{oid}",(403,404),tokens["user_002"])
        else:
            results.append(dict(check="demo_checks_not_run",passed=False,reason="目标未明确为演示服务"))
    return dict(scope="只读集成检查；不等同于端到端业务/并发/模型验收",results=results,
                passed=all(r["passed"] for r in results))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key,default in dict(business="http://127.0.0.1:8001",ai="http://127.0.0.1:8002",customer="http://127.0.0.1:8010",
                            user_frontend="http://127.0.0.1:4173",admin_frontend="http://127.0.0.1:4174").items():
        p.add_argument("--"+key.replace("_","-"),default=default)
    p.add_argument("--out",type=Path,default=Path("reports/project-smoke.json"))
    args=p.parse_args()
    report=asyncio.run(verify(args))
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report["passed"] else 1


if __name__=="__main__": raise SystemExit(main())
