import { Controller } from "@nestjs/common";
import { BaseController } from "./base.controller";

@Controller("status")
export class StatusController extends BaseController {}
