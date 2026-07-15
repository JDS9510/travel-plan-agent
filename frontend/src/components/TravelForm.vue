<template>
  <el-card shadow="hover">
    <template #header>
      <div style="display:flex;align-items:center;gap:8px;">
        <el-icon size="20" color="#409eff"><EditPen /></el-icon>
        <span style="font-weight:600;font-size:16px;">出行需求</span>
      </div>
    </template>

    <el-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-width="90px"
      label-position="right"
      size="large"
      @submit.prevent="handleSubmit"
    >
      <!-- 目的地 -->
      <el-form-item label="目的地" prop="destination">
        <el-input
          v-model="form.destination"
          placeholder="例如：成都、杭州"
          clearable
          maxlength="20"
        />
        <!-- 常用目的地快捷选项 -->
        <div class="quick-options">
          <el-tag
            v-for="city in QUICK_DESTINATIONS"
            :key="city"
            :type="form.destination === city ? 'primary' : 'info'"
            :effect="form.destination === city ? 'dark' : 'plain'"
            class="quick-tag"
            @click="form.destination = city"
          >
            {{ city }}
          </el-tag>
        </div>
      </el-form-item>

      <!-- 天数 & 预算（同行） -->
      <el-row :gutter="16">
        <el-col :span="12">
          <el-form-item label="出行天数" prop="days">
            <el-input-number
              v-model="form.days"
              :min="1"
              :max="15"
              :step="1"
              style="width:100%;"
              placeholder="3"
            />
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="总预算" prop="total_budget">
            <el-input-number
              v-model="form.total_budget"
              :min="0"
              :step="500"
              :precision="0"
              style="width:100%;"
              placeholder="2000"
            >
              <template #suffix>元</template>
            </el-input-number>
          </el-form-item>
        </el-col>
      </el-row>

      <!-- 出行人群 -->
      <el-form-item label="出行人群" prop="people">
        <el-input
          v-model="form.people"
          placeholder="例如：一家三口、朋友结伴、情侣出行"
          clearable
          maxlength="30"
        />
      </el-form-item>

      <!-- 偏好标签 -->
      <el-form-item label="偏好标签">
        <!-- 常用偏好快捷标签（一键追加） -->
        <div class="quick-options" style="margin-bottom:8px;">
          <span class="quick-label">常用：</span>
          <el-tag
            v-for="tag in QUICK_PREFERENCES"
            :key="tag"
            :type="form.preferences.includes(tag) ? 'success' : 'info'"
            :effect="form.preferences.includes(tag) ? 'dark' : 'plain'"
            class="quick-tag"
            @click="togglePreference(tag)"
          >
            {{ form.preferences.includes(tag) ? '✓ ' : '' }}{{ tag }}
          </el-tag>
        </div>
        <!-- 完整标签选择器 -->
        <el-checkbox-group
          v-model="form.preferences"
          class="tag-check-group"
        >
          <el-checkbox-button
            v-for="tag in PRESET_TAGS"
            :key="tag"
            :label="tag"
            :value="tag"
            size="default"
          />
        </el-checkbox-group>
      </el-form-item>

      <!-- 备注 -->
      <el-form-item label="备注">
        <el-input
          v-model="form.remark"
          type="textarea"
          :rows="2"
          placeholder="补充说明，如：不要早起、行程宽松一点..."
          maxlength="500"
          show-word-limit
        />
      </el-form-item>

      <!-- 提交按钮 -->
      <el-form-item>
        <el-button
          type="primary"
          size="large"
          :loading="loading"
          @click="handleSubmit"
          style="width:100%;"
        >
          <el-icon v-if="!loading"><Promotion /></el-icon>
          {{ loading ? '提交中...' : '开始规划行程' }}
        </el-button>
      </el-form-item>
    </el-form>
  </el-card>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'

// ---- Props ----
const props = defineProps({
  loading: { type: Boolean, default: false },
})

// ---- Emits ----
const emit = defineEmits(['submit'])

// ---- 预设偏好标签 ----
const PRESET_TAGS = [
  '美食', '休闲', '亲子', '历史文化', '自然风光',
  '购物', '摄影', '夜生活', '探险', '文艺',
]

/** 常用目的地快捷选项 */
const QUICK_DESTINATIONS = ['成都', '杭州', '北京']

/** 常用偏好快捷标签（最热门的 4 个） */
const QUICK_PREFERENCES = ['美食', '休闲', '历史文化', '自然风光']

/** 快捷偏好标签点击：切换选中/取消 */
function togglePreference(tag) {
  const idx = form.preferences.indexOf(tag)
  if (idx >= 0) {
    form.preferences.splice(idx, 1)
  } else {
    form.preferences.push(tag)
  }
}

// ---- 表单模型（与后端 TravelPlanRequest 字段完全对齐） ----
const formRef = ref(null)

const form = reactive({
  destination: '',
  days: 3,
  total_budget: 2000,
  people: '',
  preferences: [],
  remark: '',
})

// ---- 校验规则 ----
const rules = {
  destination: [
    { required: true, message: '请输入目的地城市', trigger: 'blur' },
  ],
  days: [
    { required: true, message: '请选择出行天数', trigger: 'change' },
  ],
  total_budget: [
    { required: true, message: '请输入总预算', trigger: 'change' },
  ],
  people: [
    { required: true, message: '请填写出行人群', trigger: 'blur' },
  ],
}

// ---- 提交 ----
async function handleSubmit() {
  if (!formRef.value) return
  try {
    await formRef.value.validate()
  } catch {
    ElMessage.warning('请完善必填项后再提交')
    return
  }

  emit('submit', {
    destination: form.destination.trim(),
    days: form.days,
    total_budget: form.total_budget,
    people: form.people.trim(),
    preferences: [...form.preferences],
    remark: form.remark.trim(),
  })
}
</script>

<style scoped>
/* 快捷选项容器 */
.quick-options {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 6px;
}

.quick-label {
  font-size: 12px;
  color: #909399;
  margin-right: 2px;
}

.quick-tag {
  cursor: pointer;
  user-select: none;
  transition: transform 0.15s;
}

.quick-tag:hover {
  transform: scale(1.05);
}
</style>
